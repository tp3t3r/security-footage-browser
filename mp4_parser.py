import struct
import os

class MP4Parser:
    """Parse MP4 container to find keyframe positions"""
    
    def __init__(self, file_path):
        self.file_path = file_path
        self.keyframes = []  # List of (sample_number, byte_offset, timestamp)
        
    def parse(self):
        """Parse MP4 structure and extract keyframe info"""
        with open(self.file_path, 'rb') as f:
            file_size = os.path.getsize(self.file_path)
            
            # Find moov box
            moov_offset, moov_size = self._find_box(f, b'moov', 0, file_size)
            if not moov_offset:
                return []
            
            # Parse moov to get keyframe info
            f.seek(moov_offset)
            moov_data = f.read(moov_size)
            
            # Find trak box (video track)
            trak_offset, trak_size = self._find_box_in_data(moov_data, b'trak')
            if not trak_offset:
                return []
            
            trak_data = moov_data[trak_offset:trak_offset + trak_size]
            
            # Find mdia box
            mdia_offset, mdia_size = self._find_box_in_data(trak_data, b'mdia')
            if not mdia_offset:
                return []
            
            mdia_data = trak_data[mdia_offset:mdia_offset + mdia_size]
            
            # Find minf box
            minf_offset, minf_size = self._find_box_in_data(mdia_data, b'minf')
            if not minf_offset:
                return []
            
            minf_data = mdia_data[minf_offset:minf_offset + minf_size]
            
            # Find stbl box (sample table)
            stbl_offset, stbl_size = self._find_box_in_data(minf_data, b'stbl')
            if not stbl_offset:
                return []
            
            stbl_data = minf_data[stbl_offset:stbl_offset + stbl_size]
            
            # Get sync samples (keyframes)
            keyframe_samples = self._parse_stss(stbl_data)
            
            # Get chunk offsets
            chunk_offsets = self._parse_stco(stbl_data)
            
            # Get sample-to-chunk mapping
            sample_to_chunk = self._parse_stsc(stbl_data)
            
            # Get sample sizes
            sample_sizes = self._parse_stsz(stbl_data)
            
            # Map keyframes to byte offsets
            self.keyframes = self._map_keyframes_to_offsets(
                keyframe_samples, chunk_offsets, sample_to_chunk, sample_sizes
            )
            
            return self.keyframes
    
    def _find_box(self, f, box_type, start, end):
        """Find box in file"""
        f.seek(start)
        while f.tell() < end:
            pos = f.tell()
            size_data = f.read(4)
            if len(size_data) < 4:
                break
            
            size = struct.unpack('>I', size_data)[0]
            box_name = f.read(4)
            
            if box_name == box_type:
                return pos + 8, size - 8
            
            if size == 0:
                break
            f.seek(pos + size)
        
        return None, None
    
    def _find_box_in_data(self, data, box_type):
        """Find box in data buffer"""
        offset = 0
        while offset < len(data) - 8:
            size = struct.unpack('>I', data[offset:offset+4])[0]
            box_name = data[offset+4:offset+8]
            
            if box_name == box_type:
                return offset + 8, size - 8
            
            if size == 0 or size > len(data):
                break
            offset += size
        
        return None, None
    
    def _parse_stss(self, stbl_data):
        """Parse stss (sync sample) box to get keyframe sample numbers"""
        offset, size = self._find_box_in_data(stbl_data, b'stss')
        if not offset:
            return []
        
        data = stbl_data[offset:offset+size]
        version = data[0]
        entry_count = struct.unpack('>I', data[4:8])[0]
        
        keyframes = []
        for i in range(entry_count):
            sample_num = struct.unpack('>I', data[8 + i*4:12 + i*4])[0]
            keyframes.append(sample_num)
        
        return keyframes
    
    def _parse_stco(self, stbl_data):
        """Parse stco (chunk offset) box"""
        offset, size = self._find_box_in_data(stbl_data, b'stco')
        if not offset:
            # Try co64 for large files
            offset, size = self._find_box_in_data(stbl_data, b'co64')
            if not offset:
                return []
            is_64bit = True
        else:
            is_64bit = False
        
        data = stbl_data[offset:offset+size]
        entry_count = struct.unpack('>I', data[4:8])[0]
        
        offsets = []
        entry_size = 8 if is_64bit else 4
        for i in range(entry_count):
            if is_64bit:
                chunk_offset = struct.unpack('>Q', data[8 + i*8:16 + i*8])[0]
            else:
                chunk_offset = struct.unpack('>I', data[8 + i*4:12 + i*4])[0]
            offsets.append(chunk_offset)
        
        return offsets
    
    def _parse_stsc(self, stbl_data):
        """Parse stsc (sample-to-chunk) box"""
        offset, size = self._find_box_in_data(stbl_data, b'stsc')
        if not offset:
            return []
        
        data = stbl_data[offset:offset+size]
        entry_count = struct.unpack('>I', data[4:8])[0]
        
        entries = []
        for i in range(entry_count):
            first_chunk = struct.unpack('>I', data[8 + i*12:12 + i*12])[0]
            samples_per_chunk = struct.unpack('>I', data[12 + i*12:16 + i*12])[0]
            entries.append((first_chunk, samples_per_chunk))
        
        return entries
    
    def _parse_stsz(self, stbl_data):
        """Parse stsz (sample size) box"""
        offset, size = self._find_box_in_data(stbl_data, b'stsz')
        if not offset:
            return []
        
        data = stbl_data[offset:offset+size]
        sample_size = struct.unpack('>I', data[4:8])[0]
        sample_count = struct.unpack('>I', data[8:12])[0]
        
        if sample_size != 0:
            # All samples same size
            return [sample_size] * sample_count
        
        # Variable sizes
        sizes = []
        for i in range(sample_count):
            size = struct.unpack('>I', data[12 + i*4:16 + i*4])[0]
            sizes.append(size)
        
        return sizes
    
    def _map_keyframes_to_offsets(self, keyframe_samples, chunk_offsets, sample_to_chunk, sample_sizes):
        """Map keyframe sample numbers to byte offsets"""
        if not keyframe_samples or not chunk_offsets or not sample_sizes:
            return []
        
        # Build sample-to-chunk map
        sample_num = 1
        chunk_map = {}  # sample_num -> (chunk_index, offset_in_chunk)
        
        for i, (first_chunk, samples_per_chunk) in enumerate(sample_to_chunk):
            next_first_chunk = sample_to_chunk[i+1][0] if i+1 < len(sample_to_chunk) else len(chunk_offsets) + 1
            
            for chunk_idx in range(first_chunk - 1, next_first_chunk - 1):
                if chunk_idx >= len(chunk_offsets):
                    break
                
                offset_in_chunk = 0
                for _ in range(samples_per_chunk):
                    if sample_num - 1 < len(sample_sizes):
                        chunk_map[sample_num] = (chunk_idx, offset_in_chunk)
                        offset_in_chunk += sample_sizes[sample_num - 1]
                        sample_num += 1
        
        # Map keyframes to offsets
        keyframe_offsets = []
        for kf_sample in keyframe_samples:
            if kf_sample in chunk_map:
                chunk_idx, offset_in_chunk = chunk_map[kf_sample]
                byte_offset = chunk_offsets[chunk_idx] + offset_in_chunk
                keyframe_offsets.append((kf_sample, byte_offset))
        
        return keyframe_offsets
    
    def get_segment_offsets(self, start_time, end_time):
        """Get byte offsets for a time range (simplified - uses sample numbers as proxy)"""
        if not self.keyframes:
            return None, None
        
        # For now, just return first and last keyframe offsets
        # In production, you'd map timestamps to samples
        if len(self.keyframes) < 2:
            return None, None
        
        return self.keyframes[0][1], self.keyframes[-1][1]
