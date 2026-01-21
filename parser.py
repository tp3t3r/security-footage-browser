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
    def __init__(self, datadirs, cache_file):
        self.datadirs = []
        for i, path in enumerate(datadirs):
            # Check if path contains info.bin (NAS structure)
            info_file = os.path.join(path, 'info.bin')
            if os.path.exists(info_file):
                # Parse info.bin to get datadir count
                datadir_count = self._parse_info_bin(info_file)
                for j in range(datadir_count):
                    datadir_path = os.path.join(path, f'datadir{j}')
                    index_file = os.path.join(datadir_path, 'index00.bin')
                    if os.path.exists(index_file):
                        self.datadirs.append({'path': datadir_path, 'index': index_file, 'num': len(self.datadirs)})
            else:
                # Direct datadir path
                index_file = os.path.join(path, 'index00.bin')
                if os.path.exists(index_file):
                    self.datadirs.append({'path': path, 'index': index_file, 'num': i})
        self.cache_file = cache_file
    
    def _parse_info_bin(self, info_file):
        with open(info_file, 'rb') as f:
            f.seek(64)
            return struct.unpack('<I', f.read(4))[0]
    
    def parse_all(self):
        segments = []
        for datadir in self.datadirs:
            segments.extend(self._parse_index(datadir))
        
        with open(self.cache_file, 'w') as f:
            json.dump(segments, f)
        
        return segments
    
    def _parse_index(self, datadir):
        with open(datadir['index'], 'rb') as f:
            header = struct.unpack('<QIIIII1172s76sI', f.read(HEADER_LEN))
            av_files = header[2]
            
            f.seek(HEADER_LEN + (av_files * FILE_LEN))
            
            files_seen = set()
            segments = []
            for file_num in range(av_files):
                for _ in range(256):
                    data = f.read(SEGMENT_LEN)
                    if len(data) < SEGMENT_LEN:
                        break
                    
                    seg_type = data[0]
                    start_time = struct.unpack('<I', data[36:40])[0]
                    end_time = struct.unpack('<I', data[40:44])[0]
                    start_offset = struct.unpack('<I', data[44:48])[0]
                    end_offset = struct.unpack('<I', data[48:52])[0]
                    
                    # Only add each file once (first valid segment)
                    if seg_type != 0 and end_time != 0 and file_num not in files_seen:
                        files_seen.add(file_num)
                        segments.append({
                            'datadir': datadir['num'],
                            'path': datadir['path'],
                            'file': file_num,
                            'start_time': start_time,
                            'end_time': end_time,
                            'start_offset': start_offset,
                            'end_offset': end_offset
                        })
                        break  # Move to next file
            return segments

class IndexWatcher(FileSystemEventHandler):
    def __init__(self, parser):
        self.parser = parser
    
    def on_modified(self, event):
        if event.src_path.endswith('index00.bin'):
            print(f"Index updated: {event.src_path}")
            self.parser.parse_all()

def run_parser(datadirs, cache_file, interval):
    parser = FootageParser(datadirs, cache_file)
    
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
    
    datadirs = config.get('storage', 'datadir_paths').split(',')
    cache_file = config.get('storage', 'cache_file')
    interval = config.getint('parser', 'interval')
    
    run_parser(datadirs, cache_file, interval)
