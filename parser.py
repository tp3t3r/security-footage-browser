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
            index_file = os.path.join(path, 'index00.bin')
            if os.path.exists(index_file):
                self.datadirs.append({'path': path, 'index': index_file, 'num': i})
        self.cache_file = cache_file
    
    def parse_all(self):
        segments = []
        for datadir in self.datadirs:
            segments.extend(self._parse_index(datadir))
        
        with open(self.cache_file, 'w') as f:
            json.dump(segments, f)
        
        return segments
    
    def _parse_index(self, datadir):
        with open(datadir['index'], 'rb') as f:
            header = struct.unpack('<QIIIII1176sI76s', f.read(HEADER_LEN))
            av_files = header[2]
            
            f.seek(HEADER_LEN + (av_files * FILE_LEN))
            
            segments = []
            for file_num in range(av_files):
                for _ in range(256):
                    data = f.read(SEGMENT_LEN)
                    if len(data) < SEGMENT_LEN:
                        break
                    
                    seg = struct.unpack('<BBxx4sQQQIIIIxxxx4s8s16s16s16s16s', data)
                    if seg[0] != 0 and seg[6] != 0:
                        segments.append({
                            'datadir': datadir['num'],
                            'path': datadir['path'],
                            'file': file_num,
                            'start_time': seg[5] & 0xFFFFFFFF,
                            'end_time': seg[6] & 0xFFFFFFFF,
                            'start_offset': seg[9],
                            'end_offset': seg[10]
                        })
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
    
    observer = Observer()
    handler = IndexWatcher(parser)
    
    for path in datadirs:
        observer.schedule(handler, path, recursive=False)
    
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
    config.read('config/app.conf')
    
    datadirs = config.get('storage', 'datadir_paths').split(',')
    cache_file = config.get('storage', 'cache_file')
    interval = config.getint('parser', 'interval')
    
    run_parser(datadirs, cache_file, interval)
