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
            mainstream_path = cam_config['path']
            substream_path = cam_config.get('substream_path', mainstream_path)
            
            # Use substream for parsing, mainstream for serving
            index_file = os.path.join(substream_path, 'index00.bin')
            if os.path.exists(index_file):
                self.datadirs.append({
                    'path': mainstream_path,  # For serving videos
                    'substream_path': substream_path,  # For parsing
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
            # Read header - matches hiktools struct FILE_IDX_HEADER
            header_data = f.read(28)
            vals = struct.unpack('<QIIIII', header_data)
            av_files = vals[2]
            
            segments = []
            files_processed = set()
            
            # Update progress with total files
            self._write_progress(camera_idx, len(self.datadirs), 0, av_files)
            
            # Process each video file from substream
            for file_num in range(av_files):
                # Update progress
                self._write_progress(camera_idx, len(self.datadirs), file_num, av_files)
                
                # Analyze substream (smaller, faster)
                substream_file = os.path.join(datadir['substream_path'], f'hiv{file_num:05d}.mp4')
                # Serve from mainstream (full quality)
                mainstream_file = os.path.join(datadir['path'], f'hiv{file_num:05d}.mp4')
                
                if not os.path.exists(substream_file) or not os.path.exists(mainstream_file):
                    continue
                
                stat = os.stat(substream_file)
                if stat.st_size <= 1024:
                    continue
                
                if file_num in files_processed:
                    continue
                files_processed.add(file_num)
                
                # Get duration from substream, serve whole file as one segment
                try:
                    dur_result = subprocess.run(
                        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                         '-of', 'default=noprint_wrappers=1:nokey=1', substream_file],
                        capture_output=True, text=True, timeout=5
                    )
                    duration = float(dur_result.stdout.strip())
                    mainstream_stat = os.stat(mainstream_file)
                    base_time = int(mainstream_stat.st_mtime)
                    
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
        observer.schedule(handler, datadir['substream_path'], recursive=False)
    
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
                'path': config.get(section, 'path'),
                'substream_path': config.get(section, 'substream_path', fallback=config.get(section, 'path'))
            })
    
    metacache_file = config.get('storage', 'metacache_file')
    interval = config.getint('parser', 'index_parse_timeout')
    
    run_parser(None, metacache_file, interval, cameras)
