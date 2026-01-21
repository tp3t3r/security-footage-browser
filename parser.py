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
    def __init__(self, datadirs, metacache_file, cameras):
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
        self.metacache_file = metacache_file
    
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
        
        with open(self.metacache_file, 'w') as f:
            json.dump(cache_data, f)
        
        return segments_by_camera
    
    def _parse_index(self, datadir):
        with open(datadir['index'], 'rb') as f:
            # Read header - matches hiktools struct FILE_IDX_HEADER
            header_data = f.read(28)
            vals = struct.unpack('<QIIIII', header_data)
            av_files = vals[2]
            
            segments = []
            files_processed = set()
            
            # Process each video file
            for file_num in range(av_files):
                video_file = os.path.join(datadir['path'], f'hiv{file_num:05d}.mp4')
                if not os.path.exists(video_file):
                    continue
                
                stat = os.stat(video_file)
                if stat.st_size <= 1024:
                    continue
                
                if file_num in files_processed:
                    continue
                files_processed.add(file_num)
                
                # Use ffprobe to detect scenes/segments
                try:
                    # Detect scene changes with ffmpeg
                    cmd = [
                        'ffmpeg', '-i', video_file,
                        '-filter:v', 'select=gt(scene\\,0.3),showinfo',
                        '-f', 'null', '-'
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    
                    # Parse scene timestamps from stderr
                    scene_times = [0.0]
                    for line in result.stderr.split('\n'):
                        if 'pts_time:' in line:
                            try:
                                pts = float(line.split('pts_time:')[1].split()[0])
                                scene_times.append(pts)
                            except:
                                pass
                    
                    # Get total duration
                    dur_result = subprocess.run(
                        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                         '-of', 'default=noprint_wrappers=1:nokey=1', video_file],
                        capture_output=True, text=True, timeout=5
                    )
                    duration = float(dur_result.stdout.strip())
                    scene_times.append(duration)
                    
                    # Create segments from scene changes
                    base_time = int(stat.st_mtime)
                    for i in range(len(scene_times) - 1):
                        start_sec = scene_times[i]
                        end_sec = scene_times[i + 1]
                        
                        # Skip very short segments (< 5 seconds)
                        if (end_sec - start_sec) < 5:
                            continue
                        
                        segments.append({
                            'file': file_num,
                            'segment': i,
                            'start_time': base_time + int(start_sec),
                            'end_time': base_time + int(end_sec),
                            'start_offset': start_sec,  # Store as time offset for ffmpeg
                            'end_offset': end_sec
                        })
                
                except Exception as e:
                    # Fallback: treat whole file as one segment
                    try:
                        dur_result = subprocess.run(
                            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                             '-of', 'default=noprint_wrappers=1:nokey=1', video_file],
                            capture_output=True, text=True, timeout=5
                        )
                        duration = float(dur_result.stdout.strip())
                        base_time = int(stat.st_mtime)
                        
                        segments.append({
                            'file': file_num,
                            'segment': 0,
                            'start_time': base_time,
                            'end_time': base_time + int(duration),
                            'start_offset': 0,
                            'end_offset': duration
                        })
                    except:
                        pass
            return segments

class IndexWatcher(FileSystemEventHandler):
    def __init__(self, parser):
        self.parser = parser
    
    def on_modified(self, event):
        if event.src_path.endswith('index00.bin'):
            print(f"Index updated: {event.src_path}")
            self.parser.parse_all()

def run_parser(datadirs, metacache_file, interval, cameras):
    parser = FootageParser(datadirs, metacache_file, cameras)
    
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
    metacache_file = config.get('storage', 'metacache_file')
    interval = config.getint('parser', 'index_parse_timeout')
    
    run_parser(datadirs, metacache_file, interval, cameras)
