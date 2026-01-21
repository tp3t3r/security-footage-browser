import struct
import os
import json
import time
import subprocess
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

HEADER_LEN = 1280
FILE_LEN = 80
SEGMENT_LEN = 128

class FootageParser:
    def __init__(self, datadirs, cache_file, cameras):
        self.datadirs = []
        for i, path in enumerate(datadirs):
            cam_name = cameras[i]['name'] if i < len(cameras) else f"Camera {i}"
            # Check if path contains info.bin (NAS structure)
            info_file = os.path.join(path, 'info.bin')
            if os.path.exists(info_file):
                # Use datadir0 (main stream)
                datadir_path = os.path.join(path, 'datadir0')
                index_file = os.path.join(datadir_path, 'index00.bin')
                if os.path.exists(index_file):
                    self.datadirs.append({'path': datadir_path, 'index': index_file, 'num': len(self.datadirs), 'name': cam_name})
            else:
                # Direct datadir path
                index_file = os.path.join(path, 'index00.bin')
                if os.path.exists(index_file):
                    self.datadirs.append({'path': path, 'index': index_file, 'num': i, 'name': cam_name})
        self.cache_file = cache_file
    
    def _parse_info_bin(self, info_file):
        with open(info_file, 'rb') as f:
            f.seek(64)
            return struct.unpack('<I', f.read(4))[0]
    
    def parse_all(self):
        segments_by_camera = {}
        for datadir in self.datadirs:
            cam_id = str(datadir['num'])
            segments_by_camera[cam_id] = self._parse_index(datadir)
        
        cache_data = {
            'cameras': [{'name': d['name'], 'path': d['path']} for d in self.datadirs],
            'segments': segments_by_camera
        }
        
        with open(self.cache_file, 'w') as f:
            json.dump(cache_data, f)
        
        return segments_by_camera
    
    def _parse_index(self, datadir):
        with open(datadir['index'], 'rb') as f:
            # Read header - matches hiktools struct FILE_IDX_HEADER
            header_data = f.read(28)
            vals = struct.unpack('<QIIIII', header_data)
            av_files = vals[2]
            
            # Skip to segment section
            f.seek(HEADER_LEN + (av_files * FILE_LEN))
            
            segments = []
            files_seen = set()
            
            for file_num in range(av_files):
                if file_num in files_seen:
                    continue
                    
                for seg_idx in range(256):
                    data = f.read(SEGMENT_LEN)
                    if len(data) < SEGMENT_LEN:
                        break
                    
                    # Parse segment: type(1) status(1) res1(2) resolution(4) startTime(8) endTime(8) ...
                    seg_type = data[0]
                    if seg_type == 0:
                        continue
                    
                    # Extract 64-bit timestamps and convert to 32-bit
                    start_time_64 = struct.unpack('<Q', data[8:16])[0]
                    end_time_64 = struct.unpack('<Q', data[16:24])[0]
                    
                    # Convert to 32-bit time_t (seconds since epoch)
                    start_time = start_time_64 & 0xFFFFFFFF
                    end_time = end_time_64 & 0xFFFFFFFF
                    
                    # Skip invalid timestamps
                    if end_time == 0 or start_time == 0:
                        continue
                    if end_time < start_time:
                        continue
                    
                    # Check if video file exists and has content
                    video_file = os.path.join(datadir['path'], f'hiv{file_num:05d}.mp4')
                    if os.path.exists(video_file):
                        stat = os.stat(video_file)
                        # Skip empty files
                        if stat.st_size > 1024:
                            # Get actual duration from video file
                            try:
                                result = subprocess.run(
                                    ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', 
                                     '-of', 'default=noprint_wrappers=1:nokey=1', video_file],
                                    capture_output=True, text=True, timeout=5
                                )
                                duration = float(result.stdout.strip())
                                # Use file modification time as start time
                                actual_start = int(stat.st_mtime)
                                actual_end = actual_start + int(duration)
                            except:
                                # Fallback to index timestamps if ffprobe fails
                                actual_start = start_time
                                actual_end = end_time
                            
                            files_seen.add(file_num)
                            segments.append({
                                'file': file_num,
                                'segment': 0,
                                'start_time': actual_start,
                                'end_time': actual_end,
                                'start_offset': 0,
                                'end_offset': stat.st_size
                            })
                    # Break after first valid segment for this file
                    files_seen.add(file_num)
                    break
            return segments

class IndexWatcher(FileSystemEventHandler):
    def __init__(self, parser):
        self.parser = parser
    
    def on_modified(self, event):
        if event.src_path.endswith('index00.bin'):
            print(f"Index updated: {event.src_path}")
            self.parser.parse_all()

def run_parser(datadirs, cache_file, interval, cameras):
    parser = FootageParser(datadirs, cache_file, cameras)
    
    if not parser.datadirs:
        print("No valid datadirs found. Exiting.")
        return
    
    observer = Observer()
    handler = IndexWatcher(parser)
    
    for datadir in parser.datadirs:
        observer.schedule(handler, datadir['path'], recursive=False)
    
    observer.start()
    
    try:
        while True:
            parser.parse_all()
            time.sleep(interval)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == '__main__':
    import sys
    import configparser
    
    config = configparser.ConfigParser()
    config.read('/etc/footage-browser/app.conf')
    
    # Parse camera sections
    cameras = []
    for section in config.sections():
        if section.startswith('camera.'):
            cameras.append({
                'name': config.get(section, 'name'),
                'path': config.get(section, 'path')
            })
    
    datadirs = [cam['path'] for cam in cameras]
    cache_file = config.get('storage', 'cache_file')
    interval = config.getint('parser', 'index_parse_timeout')
    
    run_parser(datadirs, cache_file, interval, cameras)
