import os
import re
import subprocess
from pathlib import Path
from tqdm import tqdm
import concurrent.futures
import argparse

class VideoChunker:
    def __init__(self, input_dir, output_dir="Processed_Output", target_duration=120, re_encode=False):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.target_duration = target_duration
        self.search_window_start = target_duration - 20  # 100s
        self.search_window_end = target_duration + 20    # 140s
        self.silence_threshold = "-30dB"
        self.min_silence_duration = 0.3
        self.re_encode = re_encode # New flag to control re-encoding
        
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
    
    def detect_silence(self, video_path):
        """Detect silence periods in the video. This is still a single, slow pass."""
        print(f"Analyzing silence in {video_path.name}...")
        cmd = [
            "ffmpeg", "-i", str(video_path), "-af", 
            f"silencedetect=noise={self.silence_threshold}:d={self.min_silence_duration}",
            "-f", "null", "-"
        ]
        
        result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
        silence_periods = []
        
        for line in result.stderr.split('\n'):
            if "silence_start" in line:
                start = float(line.split("silence_start:")[1].split()[0])
            elif "silence_end" in line:
                end = float(line.split("silence_end:")[1].split()[0])
                duration = float(line.split("silence_duration:")[1].split()[0])
                if duration >= self.min_silence_duration:
                    silence_periods.append((start, end, duration))
        
        return silence_periods
    
    def find_cut_points(self, video_path):
        """Find optimal cut points based on silence detection."""
        silence_periods = self.detect_silence(video_path)
        
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
        duration = float(result.stdout.strip())
        
        cut_points = [0]
        current_time = 0
        
        while current_time < duration:
            next_target = current_time + self.target_duration
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
                cut_point = min(next_target + 10, duration)
            
            if cut_point < duration:
                cut_points.append(cut_point)
                current_time = cut_point
            else:
                break
        
        return cut_points
    
    def create_chunk(self, video_path, start, end, output_path, has_silence):
        """Creates a single chunk. This function is now designed to be run in parallel."""
        cmd = [
            "ffmpeg", "-i", str(video_path),
            "-ss", str(start),
            "-to", str(end),
        ]

        if self.re_encode:
            # The old, slow way
            cmd.extend(["-c:v", "libx264", "-c:a", "aac", "-preset", "fast", "-crf", "23"])
            if not has_silence:
                fade_duration = "1.0"
                fade_start = (end - start) - float(fade_duration)
                cmd.extend([
                    "-vf", f"fade=t=out:st={fade_start}:d={fade_duration}",
                    "-af", f"afade=t=out:st={fade_start}:d={fade_duration}"
                ])
        else:
            # The new, fast way: stream copy
            cmd.extend(["-c", "copy"])
            # Note: Fades are not compatible with stream copy.
            # If you need fades, you must use the --re-encode flag.

        cmd.append(str(output_path))
        
        # Run the command. We hide the ffmpeg output to keep the terminal clean.
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        return output_path.name

    def process_videos(self):
        """Process all videos in the input directory using parallel execution."""
        video_files = list(self.input_dir.glob("*.mp4")) + list(self.input_dir.glob("*.mkv")) + list(self.input_dir.glob("*.avi"))
        
        if not video_files:
            print(f"No video files found in {self.input_dir}")
            return

        with concurrent.futures.ThreadPoolExecutor() as executor:
            for video_file in video_files:
                print(f"\n=== Processing: {video_file.name} ===")
                
                show_name, season, episode = self.extract_show_info(video_file.name)
                cut_points = self.find_cut_points(video_file)
                
                # This is a list of "tasks" we will submit to the thread pool
                futures = []
                for i in range(len(cut_points) - 1):
                    start = cut_points[i]
                    end = cut_points[i + 1]
                    part_num = i + 1
                    part_str = f"{part_num:03d}"
                    
                    output_filename = f"{show_name}_S{season}E{episode}_Part{part_str}.mp4"
                    output_path = self.output_dir / output_filename
                    
                    # Check if silence was detected for this chunk
                    # This is a simplified check; a more robust one might be needed
                    has_silence = False 
                    # For simplicity, we'll assume we can't easily check this here without re-running analysis.
                    # The key is that fades are skipped when using stream copy.
                    
                    # Submit the chunk creation task to the thread pool
                    future = executor.submit(self.create_chunk, video_file, start, end, output_path, has_silence)
                    futures.append(future)

                # Use tqdm to show a progress bar as the parallel tasks complete
                for future in tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc=f"Splitting {video_file.name}"):
                    try:
                        result_filename = future.result()
                        # print(f"Finished: {result_filename}") # Optional: print each file as it completes
                    except Exception as e:
                        print(f"Error processing a chunk: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split TV episodes into smaller chunks based on silence detection.")
    parser.add_argument("input_dir", help="Directory containing video files")
    parser.add_argument("-o", "--output", default="Processed_Output", help="Output directory (default: Processed_Output)")
    parser.add_argument("-d", "--duration", type=int, default=120, help="Target chunk duration in seconds (default: 120)")
    parser.add_argument("--re-encode", action="store_true", help="Force re-encoding for compatibility (slower). Default is fast stream copy.")
    
    args = parser.parse_args()
    
    chunker = VideoChunker(args.input_dir, args.output, args.duration, args.re_encode)
    chunker.process_videos()