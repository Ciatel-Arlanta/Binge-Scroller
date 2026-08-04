import os
import re
import subprocess
from pathlib import Path
from tqdm import tqdm
import concurrent.futures
import argparse
import json
import shutil


def _escape_subtitle_path(path):
    """Escape a filesystem path for use inside an ffmpeg subtitles filter."""
    p = str(path).replace("\\", "/")
    p = p.replace(":", "\\:").replace("'", "\\'")
    return p

class VideoChunker:
    def __init__(self, input_dir, output_dir="Processed_Output", target_duration=120, 
                 re_encode=False, strategy="silence", max_workers=4, 
                 vertical_format="blur", output_resolution="1080x1920",
                 hw_accel="auto", burn_subtitles=True, sub_lang="eng",
                 sub_index=None):
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
        self.hw_accel = hw_accel  # "auto", "nvenc", "qsv", "amf", "none"
        self.burn_subtitles = burn_subtitles  # True to burn in embedded subtitles
        self.sub_lang = sub_lang  # Preferred subtitle language tag (e.g. "eng")
        self.sub_index = sub_index  # Manual override for subtitle stream index (among subtitle streams)
        
        # Resolved encoder settings (populated by _detect_hw_encoder)
        self._hw_encoder = None
        self._hw_decode_args = []
        self._detect_hw_encoder()
        
        self.output_dir.mkdir(exist_ok=True)

    def _detect_hw_encoder(self):
        """Detect and select the best available hardware encoder"""
        if self.hw_accel == "none":
            self._hw_encoder = None
            print("Hardware acceleration: DISABLED (software encoding)")
            return

        # Check available encoders
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
            available = result.stdout
        except Exception:
            self._hw_encoder = None
            print("Hardware acceleration: ffmpeg not found, using software")
            return

        # Priority order of encoders to try
        if self.hw_accel == "auto":
            candidates = [
                ("h264_nvenc", "cuda", "NVIDIA NVENC"),
                ("h264_qsv", "qsv", "Intel QuickSync"),
                ("h264_amf", "amf", "AMD AMF"),
            ]
        elif self.hw_accel == "nvenc":
            candidates = [("h264_nvenc", "cuda", "NVIDIA NVENC")]
        elif self.hw_accel == "qsv":
            candidates = [("h264_qsv", "qsv", "Intel QuickSync")]
        elif self.hw_accel == "amf":
            candidates = [("h264_amf", "amf", "AMD AMF")]
        else:
            candidates = []

        for encoder, hwaccel, name in candidates:
            if encoder in available:
                # Verify it actually works with a quick test
                test_cmd = [
                    "ffmpeg", "-hide_banner", "-y",
                    "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1",
                    "-c:v", encoder, "-f", "null", "-"
                ]
                test = subprocess.run(test_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if test.returncode == 0:
                    self._hw_encoder = encoder
                    self._hw_decode_args = ["-hwaccel", hwaccel]
                    if hwaccel == "cuda":
                        self._hw_decode_args.extend(["-hwaccel_output_format", "nv12"])
                    print(f"Hardware acceleration: {name} ({encoder})")
                    return

        self._hw_encoder = None
        print("Hardware acceleration: No working GPU encoder found, using software (libx264)")
        
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
    
    def detect_subtitle_stream(self, video_path):
        """Detect the best subtitle stream to burn in.
        
        Returns a dict with subtitle info, or None if no suitable subtitle found.
        Result: {"sub_index": int, "codec": str, "lang": str, "title": str}
        where sub_index is the index among subtitle streams (for use as 0:s:N).
        """
        if not self.burn_subtitles:
            return None
        
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=index,codec_name,codec_type:stream_tags=language,title",
            "-of", "json",
            str(video_path)
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            info = json.loads(result.stdout)
        except (json.JSONDecodeError, KeyError):
            return None
        
        streams = info.get("streams", [])
        sub_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
        
        if not sub_streams:
            return None
        
        # If manual sub_index override is specified, use it directly
        if self.sub_index is not None:
            if self.sub_index < len(sub_streams):
                s = sub_streams[self.sub_index]
                tags = s.get("tags", {})
                return {
                    "sub_index": self.sub_index,
                    "codec": s.get("codec_name", "unknown"),
                    "lang": tags.get("language", "und"),
                    "title": tags.get("title", "Untitled"),
                }
            else:
                print(f"Warning: --sub-index {self.sub_index} out of range (found {len(sub_streams)} subtitle streams)")
                return None
        
        # Auto-detect: find the best subtitle track for the preferred language
        # Prefer "Full" tracks over "Signs/Songs" tracks
        candidates = []
        for i, s in enumerate(sub_streams):
            tags = s.get("tags", {})
            lang = tags.get("language", "und")
            title = tags.get("title", "").lower()
            codec = s.get("codec_name", "unknown")
            
            if lang == self.sub_lang:
                # Score: full subtitle tracks rank higher than signs/songs
                is_signs = any(kw in title for kw in ["sign", "song", "forced"])
                score = 0 if is_signs else 1
                candidates.append((score, i, codec, lang, tags.get("title", "Untitled")))
        
        if not candidates:
            # Fallback: try the first subtitle stream regardless of language
            s = sub_streams[0]
            tags = s.get("tags", {})
            return {
                "sub_index": 0,
                "codec": s.get("codec_name", "unknown"),
                "lang": tags.get("language", "und"),
                "title": tags.get("title", "Untitled"),
            }
        
        # Pick the best candidate (highest score, then earliest index)
        candidates.sort(key=lambda x: (-x[0], x[1]))
        best = candidates[0]
        return {
            "sub_index": best[1],
            "codec": best[2],
            "lang": best[3],
            "title": best[4],
        }

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
    
    def get_vertical_filter(self, input_width, input_height, sub_info=None, video_path=None):
        """Generate FFmpeg filter for converting to vertical format.
        
        If sub_info is provided, subtitles are overlaid onto the video first,
        then the vertical layout is built from the subtitle-composited stream.
        """
        width, height = map(int, self.output_resolution.split('x'))
        
        # Determine the source video tag
        # If subtitles are active, we first overlay them onto [0:v]
        sub_prefix = ""
        src_tag = "[0:v]"
        
        if sub_info is not None:
            si = sub_info["sub_index"]
            codec = sub_info["codec"]
            
            if "pgs" in codec or "dvd_sub" in codec or "dvb" in codec:
                # Image-based subtitles (PGS, DVD, DVB): use overlay
                sub_prefix = f"[0:v][0:s:{si}]overlay[v_with_subs];"
            else:
                # Text-based subtitles (ASS, SRT, etc): subtitles filter needs the file path
                esc = _escape_subtitle_path(video_path)
                sub_prefix = f"[0:v]subtitles='{esc}':si={si}[v_with_subs];"
            
            src_tag = "[v_with_subs]"
        
        if self.vertical_format == "none":
            if sub_info is not None:
                # Even with no vertical format, we still need to output the sub-overlaid stream
                return sub_prefix + f"{src_tag}null[out]"
            return None
        
        elif self.vertical_format == "crop":
            # Center crop - zoom in to fill vertical space
            target_width = int(input_height * width / height)
            x_offset = (input_width - target_width) // 2
            
            if sub_info is not None:
                return (
                    sub_prefix +
                    f"{src_tag}crop={target_width}:{input_height}:{x_offset}:0,"
                    f"scale={width}:{height}:flags=lanczos[out]"
                )
            return (
                f"crop={target_width}:{input_height}:{x_offset}:0,"
                f"scale={width}:{height}:flags=lanczos"
            )
        
        elif self.vertical_format == "pad":
            # Add black bars on top and bottom (letterbox)
            scaled_height = int(input_height * width / input_width)
            y_offset = (height - scaled_height) // 2
            
            if sub_info is not None:
                return (
                    sub_prefix +
                    f"{src_tag}scale={width}:{scaled_height}:flags=lanczos,"
                    f"pad={width}:{height}:0:{y_offset}:black[out]"
                )
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
            
            if sub_info is not None:
                # With subtitles: overlay subs first, then split into bg + fg
                return (
                    sub_prefix +
                    f"{src_tag}split=2[v_bg][v_fg];"
                    # Create blurred background
                    f"[v_bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},"
                    f"gblur=sigma=20[bg];"
                    # Create foreground
                    f"[v_fg]scale={width}:{scaled_height}:flags=lanczos[fg];"
                    # Overlay foreground on blurred background
                    f"[bg][fg]overlay=0:{y_offset}[out]"
                )
            
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

    def detect_silence_windowed(self, video_path, total_duration):
        """Silence detection that only analyzes audio around expected cut points.
        
        Instead of decoding the entire 1hr audio stream, we only sample ~40s windows
        around each expected cut point. For a 1hr file with 120s chunks, this analyzes
        ~30 * 40s = 20 minutes of audio instead of 60 minutes — roughly 3x faster.
        """
        print(f"Analyzing silence (windowed) in {video_path.name}...")
        
        silence_periods = []
        # Calculate expected cut positions and only analyze those windows
        expected_cuts = []
        t = self.target_duration
        while t < total_duration:
            expected_cuts.append(t)
            t += self.target_duration

        for cut_time in tqdm(expected_cuts, desc="Scanning windows", unit="window"):
            window_start = max(0, cut_time - 20)
            window_end = min(total_duration, cut_time + 20)
            
            cmd = [
                "ffmpeg",
                "-ss", str(window_start),
                "-t", str(window_end - window_start),
                "-i", str(video_path),
                "-vn",
                "-af", f"aresample=8000,silencedetect=noise={self.silence_threshold}:d={self.min_silence_duration}",
                "-f", "null", "-"
            ]
            
            result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True,
                                  stdout=subprocess.DEVNULL)
            current_start = None
            
            for line in result.stderr.split('\n'):
                if "silence_start" in line:
                    try:
                        # Timestamps are relative to the window, offset to absolute
                        current_start = float(line.split("silence_start:")[1].split()[0]) + window_start
                    except:
                        continue
                elif "silence_end" in line and current_start is not None:
                    try:
                        end = float(line.split("silence_end:")[1].split()[0]) + window_start
                        dur = float(line.split("silence_duration:")[1].split()[0])
                        if dur >= self.min_silence_duration:
                            silence_periods.append((current_start, end, dur))
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
    
    def find_cut_points_silence(self, video_path, duration, silence_periods=None):
        """Find cut points based on silence detection"""
        if silence_periods is None:
            silence_periods = self.detect_silence_windowed(video_path, duration)
        
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
    
    def find_cut_points_scene(self, video_path, duration, scene_times=None):
        """Find cut points based on scene changes"""
        if scene_times is None:
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
        """Hybrid approach: Try scene detection first, fall back to silence, then fixed.
        
        Optimized to avoid double-scanning: reuses analysis results from scene detection
        pass instead of re-running silence detection from scratch.
        """
        print(f"Using smart detection for {video_path.name}...")
        
        scene_times = self.detect_scene_changes(video_path, threshold=0.3)
        
        if len(scene_times) >= 3:
            print(f"Found {len(scene_times)} scene changes, using scene-based cuts")
            return self.find_cut_points_scene(video_path, duration, scene_times=scene_times)
        
        print("Not enough scenes, trying windowed silence detection...")
        silence_periods = self.detect_silence_windowed(video_path, duration)
        
        if len(silence_periods) >= 2:
            print(f"Found {len(silence_periods)} silence periods")
            return self.find_cut_points_silence(video_path, duration, silence_periods=silence_periods)
        
        print("Using fixed intervals as fallback")
        return self.find_cut_points_fixed(duration)
    
    def find_cut_points(self, video_path):
        """Main entry point for finding cut points"""
        duration, _, _ = self.get_video_info(video_path)

        if self.strategy == "fixed":
            cut_points = self.find_cut_points_fixed(duration)
        elif self.strategy == "scene":
            cut_points = self.find_cut_points_scene(video_path, duration)
        elif self.strategy == "smart":
            cut_points = self.find_cut_points_smart(video_path, duration)
        else:
            cut_points = self.find_cut_points_silence(video_path, duration)

        # Ensure the tail of the video is included as the final chunk.
        MIN_TAIL = 3.0
        if not cut_points:
            cut_points = [0]
        if duration - cut_points[-1] >= MIN_TAIL:
            cut_points.append(duration)
        elif len(cut_points) >= 2:
            cut_points[-1] = duration  # absorb a tiny tail into the last chunk
        return cut_points
    
    def create_chunk(self, video_path, start, end, output_path, input_width, input_height, sub_info=None):
        """Creates a single chunk with optional vertical format conversion and subtitle burn-in"""
        cmd = ["ffmpeg", "-y"]
        
        # Determine if we need to re-encode
        needs_reencode = self.re_encode or self.vertical_format != "none" or sub_info is not None
        has_subs = sub_info is not None
        
        if has_subs:
            # When burning subtitles, use -copyts for perfect subtitle sync with fast seek
            # Order: -ss (fast seek) -> -copyts -> [-hwaccel] -> -i
            cmd.extend(["-ss", str(start), "-copyts"])
            if self._hw_decode_args:
                cmd.extend(self._hw_decode_args)
            cmd.extend(["-i", str(video_path)])
        else:
            if needs_reencode and self._hw_decode_args:
                # Add hardware-accelerated decoding for faster input processing
                cmd.extend(self._hw_decode_args)
            cmd.extend([
                "-ss", str(start),
                "-i", str(video_path),
                "-t", str(end - start),
            ])
        
        if needs_reencode:
            # Get vertical format filter (with subtitle overlay if applicable)
            video_filter = self.get_vertical_filter(input_width, input_height, sub_info=sub_info, video_path=video_path)
            
            if video_filter:
                cmd.extend(["-filter_complex", video_filter])
                if has_subs:
                    # Explicit mapping required when using named filter outputs
                    cmd.extend(["-map", "[out]", "-map", "0:a:0"])
            
            # Select encoder: GPU or software
            encoder = self._hw_encoder or "libx264"
            cmd.extend(["-c:v", encoder])
            
            if self._hw_encoder and "nvenc" in self._hw_encoder:
                # NVENC-specific settings — optimized for speed + quality
                cmd.extend([
                    "-profile:v", "high",
                    "-level", "4.2",
                    "-pix_fmt", "yuv420p",
                    "-preset", "p4",       # NVENC preset: p1(fastest) to p7(best quality), p4 is balanced
                    "-tune", "hq",
                    "-rc", "vbr",
                    "-cq", "23",           # Quality level (like CRF)
                    "-maxrate", "2M",
                    "-bufsize", "4M",
                    "-movflags", "+faststart",
                ])
            elif self._hw_encoder and "qsv" in self._hw_encoder:
                # Intel QuickSync settings
                cmd.extend([
                    "-profile:v", "high",
                    "-pix_fmt", "nv12",
                    "-preset", "faster",
                    "-global_quality", "23",
                    "-maxrate", "2M",
                    "-bufsize", "4M",
                    "-movflags", "+faststart",
                ])
            elif self._hw_encoder and "amf" in self._hw_encoder:
                # AMD AMF settings
                cmd.extend([
                    "-pix_fmt", "nv12",
                    "-quality", "balanced",
                    "-rc", "vbr_latency",
                    "-qp_i", "23", "-qp_p", "23",
                    "-maxrate", "2M",
                    "-bufsize", "4M",
                    "-movflags", "+faststart",
                ])
            else:
                # Software (libx264) fallback
                cmd.extend([
                    "-profile:v", "high",
                    "-level", "4.2",
                    "-pix_fmt", "yuv420p",
                    "-preset", "veryfast",   # Faster than "faster" with minimal quality loss
                    "-crf", "23",
                    "-maxrate", "2M",
                    "-bufsize", "4M",
                    "-movflags", "+faststart",
                ])
            
            if has_subs:
                # With -copyts: output seek + duration must come after encoder settings
                cmd.extend(["-ss", str(start), "-t", str(end - start)])
            
            # Audio settings
            cmd.extend([
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
        print(f"Encoder: {self._hw_encoder or 'libx264 (software)'}")
        print(f"{'='*60}")
        
        # Get video info
        _, input_width, input_height = self.get_video_info(video_file)
        print(f"Input resolution: {input_width}x{input_height}")
        
        # Detect subtitle stream
        sub_info = self.detect_subtitle_stream(video_file)
        if sub_info:
            print(f"Subtitles: [{sub_info['codec']}] {sub_info['title']} (lang={sub_info['lang']}, track=0:s:{sub_info['sub_index']})")
        else:
            print("Subtitles: None found or disabled")
        
        show_name, season, episode = self.extract_show_info(video_file.name)
        cut_points = self.find_cut_points(video_file)
        
        print(f"Found {len(cut_points)-1} chunks to create")
        
        # Determine optimal worker count
        # For GPU encoding, fewer parallel jobs is usually better (GPU has limited encoder sessions)
        effective_workers = self.max_workers
        if self._hw_encoder:
            # NVENC typically supports 3-5 concurrent sessions (consumer GPUs)
            # More than that will queue or error
            effective_workers = min(self.max_workers, 3)
            print(f"GPU encoding: limiting to {effective_workers} parallel workers")
        
        # When burning subtitles with -copyts, limit parallel workers to avoid
        # multiple FFmpeg instances racing to decode the same subtitle stream
        if sub_info:
            effective_workers = min(effective_workers, 2)
            print(f"Subtitle burning: limiting to {effective_workers} parallel workers")
        
        # Create chunks in parallel
        futures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=effective_workers) as executor:
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
                    input_width, input_height, sub_info
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
        print(f"Encoder: {self._hw_encoder or 'libx264 (software)'}")
        print(f"Burn subtitles: {self.burn_subtitles} (lang={self.sub_lang})")
        
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
  smart   - Adaptive (tries scene -> silence -> fixed)
  scene   - Cuts at scene changes
  silence - Cuts during silence
  fixed   - Simple time-based cuts (fastest)

Hardware Acceleration:
  auto    - Auto-detect best GPU encoder (default)
  nvenc   - Force NVIDIA NVENC
  qsv     - Force Intel QuickSync
  amf     - Force AMD AMF
  none    - Disable GPU, use software (libx264)

RECOMMENDED FOR REELS APP:
  python chunker.py /input_videos -d 90 --vertical blur --resolution 1080x1920 --strategy smart

FASTEST PROCESSING (testing):
  python chunker.py /videos -d 60 --vertical blur --resolution 720x1280 --strategy fixed -w 8

Examples:
  # Full quality reels with blur background (GPU accelerated)
  python chunker.py /videos -d 75 --vertical blur --strategy smart
  
  # Fast testing with lower resolution
  python chunker.py /videos -d 60 --vertical blur --resolution 720x1280 -w 8
  
  # Center crop (loses content on sides)
  python chunker.py /videos -d 60 --vertical crop --strategy scene
  
  # Force software encoding
  python chunker.py /videos -d 60 --vertical blur --hw-accel none
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
    parser.add_argument("--hw-accel", choices=["auto", "nvenc", "qsv", "amf", "none"],
                       default="auto",
                       help="Hardware acceleration method (default: auto)")
    parser.add_argument("--subtitles", action=argparse.BooleanOptionalAction,
                       default=True,
                       help="Burn in embedded subtitles (default: True, use --no-subtitles to disable)")
    parser.add_argument("--sub-lang", default="eng",
                       help="Preferred subtitle language tag (default: eng)")
    parser.add_argument("--sub-index", type=int, default=None,
                       help="Manual subtitle stream index override (among subtitle streams, e.g. 0 for first)")
    
    args = parser.parse_args()
    
    chunker = VideoChunker(
        args.input_dir, 
        args.output, 
        args.duration, 
        args.re_encode,
        args.strategy,
        args.workers,
        args.vertical,
        args.resolution,
        args.hw_accel,
        args.subtitles,
        args.sub_lang,
        args.sub_index
    )
    chunker.process_videos()