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
        for i, cam_config in enumerate(cameras):
            path = cam_config['path']
            index_file = os.path.join(path, 'index00.bin')
            if os.path.exists(index_file):
                self.datadirs.append({
                    'path': path,
                    'index': index_file,
                    'num': i,
                    'name': cam_config['name']
                })
        self.metacache_file = metacache_file
    
    def _parse_info_bin(self, info_file):
        with open(info_file, 'rb') as f:
            f.seek(64)
            return struct.unpack('<I', f.read(4))[0]
    
    def parse_all(self):
        segments_by_camera = {}
        
        # Write initial progress
        self._write_progress(0, len(self.datadirs), 0, 0)
        
        for idx, datadir in enumerate(self.datadirs):
            cam_id = str(datadir['num'])
            segments_by_camera[cam_id] = self._parse_index(datadir, idx)
        
        cache_data = {
            'cameras': [{'name': d['name'], 'path': d['path']} for d in self.datadirs],
            'segments': segments_by_camera
        }
        
        with open(self.metacache_file, 'w') as f:
            json.dump(cache_data, f)
        
        # Clear progress file
        progress_file = self.metacache_file.replace('.json', '.progress')
        if os.path.exists(progress_file):
            os.remove(progress_file)
        
        return segments_by_camera
    
    def _write_progress(self, camera_idx, total_cameras, files_done, total_files):
        progress_file = self.metacache_file.replace('.json', '.progress')
        progress = {
            'camera': camera_idx,
            'total_cameras': total_cameras,
            'files_done': files_done,
            'total_files': total_files,
            'timestamp': time.time()
        }
        with open(progress_file, 'w') as f:
            json.dump(progress, f)
    
    def _parse_index(self, datadir, camera_idx):
        with open(datadir['index'], 'rb') as f:
            # Read header
            header_data = f.read(28)
            vals = struct.unpack('<QIIIII', header_data)
            av_files = vals[2]
            
            # Skip to segment section
            f.seek(HEADER_LEN + (av_files * FILE_LEN))
            
            segments = []
            
            # Update progress with total files
            self._write_progress(camera_idx, len(self.datadirs), 0, av_files)
            
            # Process each file's segments
            for file_num in range(av_files):
                # Update progress
                self._write_progress(camera_idx, len(self.datadirs), file_num, av_files)
                
                video_file = os.path.join(datadir['path'], f'hiv{file_num:05d}.mp4')
                if not os.path.exists(video_file):
                    # Skip all 256 segments for this file
                    f.seek(256 * SEGMENT_LEN, 1)
                    continue
                
                stat = os.stat(video_file)
                if stat.st_size <= 1024:
                    f.seek(256 * SEGMENT_LEN, 1)
                    continue
                
                # Read 256 segment records for this file
                for seg_idx in range(256):
                    data = f.read(SEGMENT_LEN)
                    if len(data) < SEGMENT_LEN:
                        break
                    
                    seg_type = data[0]
                    if seg_type == 0:
                        continue
                    
                    # Extract timestamps and offsets
                    start_time_64 = struct.unpack('<Q', data[8:16])[0]
                    end_time_64 = struct.unpack('<Q', data[16:24])[0]
                    start_offset = struct.unpack('<I', data[36:40])[0]
                    end_offset = struct.unpack('<I', data[40:44])[0]
                    
                    # Convert timestamps
                    start_time = start_time_64 & 0xFFFFFFFF
                    end_time = end_time_64 & 0xFFFFFFFF
                    
                    # Skip invalid timestamps
                    if end_time == 0 or start_time == 0:
                        continue
                    if end_time < start_time:
                        continue
                    
                    # Skip placeholder offsets (0,1 or invalid)
                    if start_offset >= end_offset or end_offset > stat.st_size or (end_offset - start_offset) < 1024:
                        continue
                    
                    segments.append({
                        'file': file_num,
                        'segment': seg_idx,
                        'start_time': start_time,
                        'end_time': end_time,
                        'start_offset': start_offset,
                        'end_offset': end_offset
                    })
            
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
    
    metacache_file = config.get('storage', 'metacache_file')
    interval = config.getint('parser', 'index_parse_timeout')
    
    run_parser(None, metacache_file, interval, cameras)
