import os
import re
import subprocess
from pathlib import Path
from tqdm import tqdm
import concurrent.futures
import argparse
import json

class VideoChunker:
    def __init__(self, input_dir, output_dir="Processed_Output", target_duration=120, 
                 re_encode=False, strategy="silence", max_workers=4, 
                 vertical_format="blur", output_resolution="1080x1920"):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.target_duration = target_duration
        self.search_window_start = target_duration - 20
        self.search_window_end = target_duration + 20
        self.silence_threshold = "-30dB"
        self.min_silence_duration = 0.3
        self.re_encode = re_encode
        self.strategy = strategy
        self.max_workers = max_workers
        self.vertical_format = vertical_format  # "blur", "crop", "pad", or "none"
        self.output_resolution = output_resolution  # "1080x1920" (9:16) or "720x1280"
        
        self.output_dir.mkdir(exist_ok=True)
        
    def extract_show_info(self, filename):
        """Extract show name, season, and episode from filename"""
        patterns = [
            r"(.*?)[._\s]S(\d+)E(\d+)",
            r"(.*?)[._\s](\d+)x(\d+)",
            r"(.*?)[._\s]Season(\d+)Episode(\d+)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, filename, re.IGNORECASE)
            if match:
                show_name = match.group(1).replace(".", " ").replace("_", " ").strip()
                season = match.group(2).zfill(2)
                episode = match.group(3).zfill(2)
                return show_name, season, episode
        
        show_name = Path(filename).stem
        return show_name, "01", "01"
    
    def get_video_info(self, video_path):
        """Get video duration and dimensions"""
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json",
            str(video_path)
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
        info = json.loads(result.stdout)
        
        width = info['streams'][0]['width']
        height = info['streams'][0]['height']
        duration = float(info['format']['duration'])
        
        return duration, width, height
    
    def get_vertical_filter(self, input_width, input_height):
        """Generate FFmpeg filter for converting to vertical format"""
        width, height = map(int, self.output_resolution.split('x'))
        
        if self.vertical_format == "none":
            return None
        
        elif self.vertical_format == "crop":
            # Center crop - zoom in to fill vertical space
            # Calculate the width needed for 9:16 aspect ratio
            target_width = int(input_height * width / height)
            x_offset = (input_width - target_width) // 2
            
            return (
                f"crop={target_width}:{input_height}:{x_offset}:0,"
                f"scale={width}:{height}:flags=lanczos"
            )
        
        elif self.vertical_format == "pad":
            # Add black bars on top and bottom (letterbox)
            # Scale to fit width, then pad height
            scaled_height = int(input_height * width / input_width)
            y_offset = (height - scaled_height) // 2
            
            return (
                f"scale={width}:{scaled_height}:flags=lanczos,"
                f"pad={width}:{height}:0:{y_offset}:black"
            )
        
        elif self.vertical_format == "blur":
            # INSTAGRAM/TIKTOK STYLE: Blurred background + centered video
            # This is the most popular format for landscape→portrait conversion
            
            # Calculate scaled dimensions to fit the video centered
            scaled_height = int(input_height * width / input_width)
            y_offset = (height - scaled_height) // 2
            
            return (
                # Create blurred background: scale to fill, blur heavily
                f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},"
                f"gblur=sigma=20[bg];"
                # Create foreground: scale to fit with letterbox
                f"[0:v]scale={width}:{scaled_height}:flags=lanczos[fg];"
                # Overlay foreground on blurred background
                f"[bg][fg]overlay=0:{y_offset}"
            )
        
        return None
    
    def detect_scene_changes(self, video_path, threshold=0.4):
        """Detect scene changes using ffmpeg's scene detection filter (FAST)"""
        print(f"Detecting scenes in {video_path.name}...")
        
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-filter:v", f"select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-"
        ]
        
        result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, 
                              stdout=subprocess.DEVNULL)
        
        scene_times = []
        for line in result.stderr.split('\n'):
            if "pts_time:" in line:
                try:
                    time = float(line.split("pts_time:")[1].split()[0])
                    scene_times.append(time)
                except:
                    continue
        
        return sorted(scene_times)
    
    def detect_silence_fast(self, video_path):
        """Faster silence detection with reduced quality for speed"""
        print(f"Analyzing silence in {video_path.name}...")
        
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-vn",
            "-af", f"aresample=8000,silencedetect=noise={self.silence_threshold}:d={self.min_silence_duration}",
            "-f", "null", "-"
        ]
        
        result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True,
                              stdout=subprocess.DEVNULL)
        silence_periods = []
        current_start = None
        
        for line in result.stderr.split('\n'):
            if "silence_start" in line:
                try:
                    current_start = float(line.split("silence_start:")[1].split()[0])
                except:
                    continue
            elif "silence_end" in line and current_start is not None:
                try:
                    end = float(line.split("silence_end:")[1].split()[0])
                    duration = float(line.split("silence_duration:")[1].split()[0])
                    if duration >= self.min_silence_duration:
                        silence_periods.append((current_start, end, duration))
                    current_start = None
                except:
                    continue
        
        return silence_periods
    
    def find_cut_points_fixed(self, duration):
        """Simple fixed-interval cutting (FASTEST)"""
        cut_points = []
        current = 0
        while current < duration:
            cut_points.append(current)
            current += self.target_duration
        return cut_points
    
    def find_cut_points_silence(self, video_path, duration):
        """Find cut points based on silence detection"""
        silence_periods = self.detect_silence_fast(video_path)
        
        cut_points = [0]
        current_time = 0
        
        while current_time < duration:
            search_start = current_time + self.search_window_start
            search_end = min(current_time + self.search_window_end, duration)
            
            best_silence = None
            for start, end, dur in silence_periods:
                if search_start <= start <= search_end:
                    if best_silence is None or dur > best_silence[2]:
                        best_silence = (start, end, dur)
            
            if best_silence:
                cut_point = best_silence[0] + (best_silence[1] - best_silence[0]) / 2
            else:
                cut_point = min(current_time + self.target_duration, duration)
            
            if cut_point > current_time and cut_point < duration:
                cut_points.append(cut_point)
                current_time = cut_point
            else:
                break
        
        return cut_points
    
    def find_cut_points_scene(self, video_path, duration):
        """Find cut points based on scene changes"""
        scene_times = self.detect_scene_changes(video_path)
        
        if not scene_times:
            print("No scenes detected, falling back to fixed intervals")
            return self.find_cut_points_fixed(duration)
        
        cut_points = [0]
        current_time = 0
        
        while current_time < duration:
            search_start = current_time + self.search_window_start
            search_end = min(current_time + self.search_window_end, duration)
            
            best_scene = None
            best_distance = float('inf')
            target = current_time + self.target_duration
            
            for scene_time in scene_times:
                if search_start <= scene_time <= search_end:
                    distance = abs(scene_time - target)
                    if distance < best_distance:
                        best_distance = distance
                        best_scene = scene_time
            
            if best_scene:
                cut_point = best_scene
            else:
                cut_point = min(current_time + self.target_duration, duration)
            
            if cut_point > current_time and cut_point < duration:
                cut_points.append(cut_point)
                current_time = cut_point
            else:
                break
        
        return cut_points
    
    def find_cut_points_smart(self, video_path, duration):
        """Hybrid approach: Try scene detection first, fall back to silence, then fixed"""
        print(f"Using smart detection for {video_path.name}...")
        
        scene_times = self.detect_scene_changes(video_path, threshold=0.3)
        
        if len(scene_times) >= 3:
            print(f"Found {len(scene_times)} scene changes, using scene-based cuts")
            return self.find_cut_points_scene(video_path, duration)
        
        print("Not enough scenes, trying silence detection...")
        silence_periods = self.detect_silence_fast(video_path)
        
        if len(silence_periods) >= 2:
            print(f"Found {len(silence_periods)} silence periods")
            return self.find_cut_points_silence(video_path, duration)
        
        print("Using fixed intervals as fallback")
        return self.find_cut_points_fixed(duration)
    
    def find_cut_points(self, video_path):
        """Main entry point for finding cut points"""
        duration, _, _ = self.get_video_info(video_path)
        
        if self.strategy == "fixed":
            return self.find_cut_points_fixed(duration)
        elif self.strategy == "scene":
            return self.find_cut_points_scene(video_path, duration)
        elif self.strategy == "smart":
            return self.find_cut_points_smart(video_path, duration)
        else:
            return self.find_cut_points_silence(video_path, duration)
    
    def create_chunk(self, video_path, start, end, output_path, input_width, input_height):
        """Creates a single chunk with optional vertical format conversion"""
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(end - start),
        ]
        
        # Determine if we need to re-encode
        needs_reencode = self.re_encode or self.vertical_format != "none"
        
        if needs_reencode:
            # Get vertical format filter if needed
            video_filter = self.get_vertical_filter(input_width, input_height)
            
            if video_filter:
                cmd.extend(["-filter_complex", video_filter])
            
            # Video encoding settings optimized for mobile
            cmd.extend([
                "-c:v", "libx264",
                "-profile:v", "high",
                "-level", "4.2",
                "-pix_fmt", "yuv420p",
                "-preset", "faster",
                "-crf", "23",
                "-maxrate", "2M",  # Limit bitrate for mobile streaming
                "-bufsize", "4M",
                "-movflags", "+faststart",
                # Audio settings
                "-c:a", "aac",
                "-ac", "2",
                "-ar", "44100",
                "-b:a", "128k"
            ])
        else:
            # Fast stream copy
            cmd.extend([
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                "-movflags", "+faststart"
            ])
        
        cmd.append(str(output_path))
        
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return output_path.name

    def process_single_video(self, video_file):
        """Process a single video file"""
        print(f"\n{'='*60}")
        print(f"Processing: {video_file.name}")
        print(f"Strategy: {self.strategy}")
        print(f"Vertical format: {self.vertical_format}")
        print(f"Output resolution: {self.output_resolution}")
        print(f"{'='*60}")
        
        # Get video info
        _, input_width, input_height = self.get_video_info(video_file)
        print(f"Input resolution: {input_width}x{input_height}")
        
        show_name, season, episode = self.extract_show_info(video_file.name)
        cut_points = self.find_cut_points(video_file)
        
        print(f"Found {len(cut_points)-1} chunks to create")
        
        # Create chunks in parallel
        futures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for i in range(len(cut_points) - 1):
                start = cut_points[i]
                end = cut_points[i + 1]
                part_num = i + 1
                part_str = f"{part_num:03d}"
                
                output_filename = f"{show_name}_S{season}E{episode}_Part{part_str}.mp4"
                output_path = self.output_dir / output_filename
                
                future = executor.submit(
                    self.create_chunk, 
                    video_file, start, end, output_path,
                    input_width, input_height
                )
                futures.append(future)

            for future in tqdm(concurrent.futures.as_completed(futures), 
                             total=len(futures), 
                             desc=f"Creating chunks",
                             unit="chunk"):
                try:
                    future.result()
                except Exception as e:
                    print(f"\nError processing chunk: {e}")

    def process_videos(self):
        """Process all videos in the input directory"""
        video_extensions = ["*.mp4", "*.mkv", "*.avi", "*.mov"]
        video_files = []
        for ext in video_extensions:
            video_files.extend(self.input_dir.glob(ext))
        
        if not video_files:
            print(f"No video files found in {self.input_dir}")
            return

        print(f"\nFound {len(video_files)} video file(s) to process")
        print(f"Output directory: {self.output_dir}")
        print(f"Target duration: {self.target_duration}s")
        print(f"Vertical format: {self.vertical_format}")
        print(f"Output resolution: {self.output_resolution}")
        print(f"Strategy: {self.strategy}")
        print(f"Max workers: {self.max_workers}")
        
        for video_file in video_files:
            self.process_single_video(video_file)
        
        print(f"\n{'='*60}")
        print("All videos processed successfully!")
        print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Split TV episodes into vertical mobile-ready chunks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Vertical Format Options:
  blur    - RECOMMENDED: Instagram/TikTok style with blurred background
  crop    - Center crop and zoom to fill vertical space (loses sides)
  pad     - Add black bars (letterbox) - NOT recommended for reels
  none    - Keep original landscape format

Output Resolutions:
  1080x1920 - Full HD vertical (9:16) - RECOMMENDED
  720x1280  - HD vertical (9:16) - faster processing, smaller files

Chunking Strategies:
  smart   - Adaptive (tries scene → silence → fixed)
  scene   - Cuts at scene changes
  silence - Cuts during silence
  fixed   - Simple time-based cuts (fastest)

RECOMMENDED FOR REELS APP:
  python chunker.py /input_videos -d 90 --vertical blur --resolution 1080x1920 --strategy smart

FASTEST PROCESSING (testing):
  python chunker.py /videos -d 60 --vertical blur --resolution 720x1280 --strategy fixed -w 8

Examples:
  # Full quality reels with blur background
  python chunker.py /videos -d 75 --vertical blur --strategy smart
  
  # Fast testing with lower resolution
  python chunker.py /videos -d 60 --vertical blur --resolution 720x1280 -w 8
  
  # Center crop (loses content on sides)
  python chunker.py /videos -d 60 --vertical crop --strategy scene
        """
    )
    
    parser.add_argument("input_dir", help="Directory containing video files")
    parser.add_argument("-o", "--output", default="Processed_Output", 
                       help="Output directory (default: Processed_Output)")
    parser.add_argument("-d", "--duration", type=int, default=60, 
                       help="Target chunk duration in seconds (default: 60)")
    parser.add_argument("--vertical", choices=["blur", "crop", "pad", "none"],
                       default="blur",
                       help="Vertical format conversion method (default: blur)")
    parser.add_argument("--resolution", default="1080x1920",
                       choices=["1080x1920", "720x1280"],
                       help="Output resolution (default: 1080x1920)")
    parser.add_argument("--re-encode", action="store_true", 
                       help="Force re-encoding even without vertical conversion")
    parser.add_argument("--strategy", choices=["fixed", "scene", "silence", "smart"],
                       default="smart",
                       help="Chunking strategy (default: smart)")
    parser.add_argument("-w", "--workers", type=int, default=4,
                       help="Max parallel chunk workers (default: 4)")
    
    args = parser.parse_args()
    
    chunker = VideoChunker(
        args.input_dir, 
        args.output, 
        args.duration, 
        args.re_encode,
        args.strategy,
        args.workers,
        args.vertical,
        args.resolution
    )
    chunker.process_videos()