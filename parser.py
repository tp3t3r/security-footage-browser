import struct
import os
import json
import time
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
                # Only use datadir1 (substream for testing)
                datadir_path = os.path.join(path, 'datadir1')
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
        segments = []
        for datadir in self.datadirs:
            segments.extend(self._parse_index(datadir))
        
        # Structure: cameras list + segments with camera references
        cache_data = {
            'cameras': [{'id': d['num'], 'name': d['name'], 'path': d['path']} for d in self.datadirs],
            'segments': segments
        }
        
        with open(self.cache_file, 'w') as f:
            json.dump(cache_data, f)
        
        return segments
    
    def _parse_index(self, datadir):
        with open(datadir['index'], 'rb') as f:
            # Read header - match PHP: Q1modifyTimes/I1version/I1avFiles/...
            f.seek(0)
            header_data = f.read(HEADER_LEN)
            av_files = struct.unpack('<xxIxx', header_data[8:16])[0]
            
            # Skip to segment section
            f.seek(HEADER_LEN + (av_files * FILE_LEN))
            
            segments = []
            for file_num in range(av_files):
                for seg_idx in range(256):
                    data = f.read(SEGMENT_LEN)
                    if len(data) < SEGMENT_LEN:
                        break
                    
                    # Parse segment structure
                    seg_type = data[0]
                    if seg_type == 0:
                        continue
                    
                    # Extract timestamps and offsets (matching PHP unpack)
                    start_time = struct.unpack('<Q', data[8:16])[0] & 0xFFFFFFFF
                    end_time = struct.unpack('<Q', data[16:24])[0] & 0xFFFFFFFF
                    
                    if end_time == 0:
                        continue
                    
                    start_offset = struct.unpack('<I', data[36:40])[0]
                    end_offset = struct.unpack('<I', data[40:44])[0]
                    
                    video_file = os.path.join(datadir['path'], f'hiv{file_num:05d}.mp4')
                    if os.path.exists(video_file):
                        segments.append({
                            'camera_id': datadir['num'],
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
    interval = config.getint('parser', 'interval')
    
    run_parser(datadirs, cache_file, interval, cameras)
