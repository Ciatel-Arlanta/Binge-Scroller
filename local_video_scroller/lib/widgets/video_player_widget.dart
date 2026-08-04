import 'dart:io';
import 'package:flutter/material.dart';
import 'package:video_player/video_player.dart';
import '../models/video_model.dart';

class VideoPlayerWidget extends StatefulWidget {
  final VideoModel video;
  final bool autoPlay;
  final Function()? onVideoEnd;
  const VideoPlayerWidget({
    Key? key,
    required this.video,
    this.autoPlay = false,
    this.onVideoEnd,
  }) : super(key: key);

  @override
  _VideoPlayerWidgetState createState() => _VideoPlayerWidgetState();
}

class _VideoPlayerWidgetState extends State<VideoPlayerWidget> {
  VideoPlayerController? _controller;
  bool _isInitialized = false;
  bool _hasError = false;
  bool _isDisposed = false;
  bool _showSeekIndicator = false;
  String _seekDirection = '';
  bool _shouldPause = false; // Track if video should be paused
  bool _hasEnded = false; // Latch so onVideoEnd fires once per playthrough

  @override
  void initState() {
    super.initState();
    _initializeVideo();
  }

  @override
  void didUpdateWidget(VideoPlayerWidget oldWidget) {
    super.didUpdateWidget(oldWidget);

    // Handle video path change
    if (oldWidget.video.path != widget.video.path) {
      _disposeController();
      _initializeVideo();
    }
    // Handle play/pause when scrolling
    else if (oldWidget.autoPlay != widget.autoPlay) {
      _shouldPause = !widget.autoPlay;
      _updatePlaybackState();
    }
  }

  Future<void> _updatePlaybackState() async {
    if (_controller != null && _isInitialized && !_isDisposed && mounted) {
      try {
        if (widget.autoPlay && !_shouldPause) {
          await _controller!.play();
        } else {
          await _controller!.pause();
        }
      } catch (e) {
        print('Error updating playback state: $e');
      }
    }
  }

  void _onControllerUpdate() {
    if (_isDisposed || _controller == null) return;
    final value = _controller!.value;
    if (!_hasEnded &&
        value.duration > Duration.zero &&
        value.position >= value.duration &&
        widget.onVideoEnd != null &&
        widget.autoPlay) {
      _hasEnded = true;
      widget.onVideoEnd!();
    }
  }

  Future<void> _initializeVideo() async {
    VideoPlayerController? controller;
    try {
      _hasEnded = false;
      controller = VideoPlayerController.file(
        File(widget.video.path),
        videoPlayerOptions: VideoPlayerOptions(mixWithOthers: true),
      );
      _controller = controller;

      await controller.initialize();
      // Bail if disposed or a newer init replaced this controller mid-await.
      // Do not dispose here: _disposeController already owns teardown and
      // would double-dispose if we disposed the same instance again.
      if (!mounted || _isDisposed || _controller != controller) {
        return;
      }

      await controller.setVolume(1.0);
      if (!mounted || _isDisposed || _controller != controller) {
        return;
      }

      controller.addListener(_onControllerUpdate);

      if (!_isDisposed && mounted && _controller == controller) {
        setState(() {
          _isInitialized = true;
        });
      }

      // Re-check autoPlay after async init — user may have scrolled away
      if (widget.autoPlay &&
          !_shouldPause &&
          !_isDisposed &&
          mounted &&
          _controller == controller) {
        await controller.play();
      }
    } catch (e) {
      print('Error initializing video: $e');
      if (!_isDisposed && mounted &&
          controller != null &&
          _controller == controller) {
        setState(() {
          _hasError = true;
        });
      }
    }
  }

  void _disposeController() {
    if (_controller != null) {
      _shouldPause = true;
      _controller!.pause();
      _controller!.dispose();
      _controller = null;
      _isInitialized = false;
      _hasError = false;
      _hasEnded = false;
    }
  }

  @override
  void dispose() {
    _isDisposed = true;
    _shouldPause = true;
    _disposeController();
    super.dispose();
  }

  void _togglePlayPause() {
    if (_controller != null && _isInitialized && !_isDisposed) {
      setState(() {
        if (_controller!.value.isPlaying) {
          _shouldPause = true;
          _controller!.pause();
        } else {
          // Replay from start if already at end
          final value = _controller!.value;
          if (value.duration > Duration.zero &&
              value.position >= value.duration) {
            _hasEnded = false;
            _controller!.seekTo(Duration.zero);
          }
          _shouldPause = false;
          _controller!.play();
        }
      });
    }
  }

  void _seekVideo(bool forward) {
    if (_controller != null && _isInitialized && !_isDisposed) {
      final currentPosition = _controller!.value.position;
      final duration = _controller!.value.duration;

      final seekAmount = const Duration(seconds: 5);
      Duration newPosition;

      if (forward) {
        newPosition = currentPosition + seekAmount;
        if (newPosition > duration) {
          newPosition = duration;
        }
        _seekDirection = '+5s';
      } else {
        newPosition = currentPosition - seekAmount;
        if (newPosition < Duration.zero) {
          newPosition = Duration.zero;
        }
        _seekDirection = '-5s';
        // Seeking backwards allows completion to fire again
        _hasEnded = false;
      }

      _controller!.seekTo(newPosition);

      // Show seek indicator
      setState(() {
        _showSeekIndicator = true;
      });

      // Hide seek indicator after 1 second
      Future.delayed(const Duration(seconds: 1), () {
        if (mounted) {
          setState(() {
            _showSeekIndicator = false;
          });
        }
      });
    }
  }

  String _formatDuration(Duration duration) {
    String twoDigits(int n) => n.toString().padLeft(2, '0');
    String twoDigitMinutes = twoDigits(duration.inMinutes.remainder(60));
    String twoDigitSeconds = twoDigits(duration.inSeconds.remainder(60));
    return "${twoDigits(duration.inHours)}:$twoDigitMinutes:$twoDigitSeconds";
  }

  @override
  Widget build(BuildContext context) {
    if (_hasError) {
      return Container(
        color: Colors.black,
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.error_outline, color: Colors.white, size: 48),
              const SizedBox(height: 16),
              Text(
                'Error loading video',
                style: TextStyle(color: Colors.white),
              ),
              const SizedBox(height: 16),
              Text(
                widget.video.displayName,
                style: TextStyle(color: Colors.white),
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      );
    }

    if (!_isInitialized) {
      return Container(
        color: Colors.black,
        child: const Center(
          child: CircularProgressIndicator(color: Colors.white),
        ),
      );
    }

    // Check if video should be paused but is still playing
    if (_shouldPause && _controller!.value.isPlaying && !_isDisposed) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _controller != null) {
          _controller!.pause();
        }
      });
    }

    return GestureDetector(
      onTap: _togglePlayPause,
      onDoubleTap: () {
        // Default seek forward on double tap
        _seekVideo(true);
      },
      child: Container(
        color: Colors.black,
        child: Stack(
          fit: StackFit.expand,
          children: [
            // Video player
            FittedBox(
              fit: BoxFit.cover,
              child: SizedBox(
                width: _controller!.value.size.width,
                height: _controller!.value.size.height,
                child: VideoPlayer(_controller!),
              ),
            ),

            // Seek indicator
            if (_showSeekIndicator)
              Positioned(
                top: 50,
                left: 0,
                right: 0,
                child: Center(
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 8,
                    ),
                    decoration: BoxDecoration(
                      color: Colors.black87,
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          _seekDirection.startsWith('+')
                              ? Icons.fast_forward
                              : Icons.fast_rewind,
                          color: Colors.white,
                          size: 24,
                        ),
                        const SizedBox(width: 8),
                        Text(
                          _seekDirection,
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),

            // Video title (shown when paused) — reactive to controller ticks
            ValueListenableBuilder<VideoPlayerValue>(
              valueListenable: _controller!,
              builder: (context, value, child) {
                if (value.isPlaying) {
                  return const SizedBox.shrink();
                }
                return Positioned(
                  bottom: 80, // Moved up to make room for progress bar
                  left: 0,
                  right: 0,
                  child: Center(
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 8,
                      ),
                      decoration: BoxDecoration(
                        color: Colors.black54,
                        borderRadius: BorderRadius.circular(20),
                      ),
                      child: Text(
                        widget.video.displayName,
                        style: const TextStyle(color: Colors.white),
                      ),
                    ),
                  ),
                );
              },
            ),

            // Progress bar and time display
            Positioned(
              bottom: 0,
              left: 0,
              right: 0,
              child: Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: 16,
                  vertical: 8,
                ),
                decoration: const BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [Colors.transparent, Colors.black54],
                  ),
                ),
                child: Column(
                  children: [
                    // Progress bar
                    ValueListenableBuilder<VideoPlayerValue>(
                      valueListenable: _controller!,
                      builder: (context, value, child) {
                        final durMs = value.duration.inMilliseconds;
                        final progress = durMs > 0
                            ? (value.position.inMilliseconds / durMs)
                                .clamp(0.0, 1.0)
                            : 0.0;
                        return LinearProgressIndicator(
                          value: progress,
                          backgroundColor: Colors.white24,
                          valueColor: const AlwaysStoppedAnimation<Color>(
                            Colors.red,
                          ),
                          minHeight: 3,
                        );
                      },
                    ),
                    const SizedBox(height: 4),
                    // Time display
                    ValueListenableBuilder<VideoPlayerValue>(
                      valueListenable: _controller!,
                      builder: (context, value, child) {
                        return Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            Text(
                              _formatDuration(value.position),
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 12,
                              ),
                            ),
                            Text(
                              _formatDuration(value.duration),
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 12,
                              ),
                            ),
                          ],
                        );
                      },
                    ),
                  ],
                ),
              ),
            ),

            // Tap zones for seeking (optional - double tap anywhere to seek forward)
            Positioned(
              left: 0,
              right: 0,
              top: 0,
              bottom: 0,
              child: Row(
                children: [
                  // Left side - double tap to rewind
                  Expanded(
                    child: GestureDetector(
                      onDoubleTap: () => _seekVideo(false),
                      child: Container(color: Colors.transparent),
                    ),
                  ),
                  // Right side - double tap to fast forward
                  Expanded(
                    child: GestureDetector(
                      onDoubleTap: () => _seekVideo(true),
                      child: Container(color: Colors.transparent),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}