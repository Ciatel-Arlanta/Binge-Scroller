import 'dart:io';
import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:preload_page_view/preload_page_view.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/video_model.dart';
import '../services/video_service.dart';
import '../widgets/video_player_widget.dart';

class VideoFeedScreen extends StatefulWidget {
  const VideoFeedScreen({Key? key}) : super(key: key);

  @override
  _VideoFeedScreenState createState() => _VideoFeedScreenState();
}

class _VideoFeedScreenState extends State<VideoFeedScreen> {
  final VideoService _videoService = VideoService();
  List<VideoModel> _allVideos = [];
  bool _isLoading = true;
  bool _hasPermission = false;
  int _currentIndex = 0;
  late PreloadPageController _pageController;

  @override
  void initState() {
    super.initState();
    _pageController = PreloadPageController();
    _checkPermissionAndLoadVideos();
    _loadLastWatchedVideo();
  }

  Future<void> _checkPermissionAndLoadVideos() async {
    // Check for storage permission
    var status = await Permission.storage.status;
    
    if (!status.isGranted) {
      // Request permission
      status = await Permission.storage.request();
      
      if (!status.isGranted) {
        // Show dialog to explain why permission is needed
        _showPermissionDialog();
        return;
      }
    }

    setState(() {
      _hasPermission = true;
    });

    await _loadVideos();
  }

  void _showPermissionDialog() {
    showDialog(
      context: context,
      builder: (BuildContext context) {
        return AlertDialog(
          title: const Text('Storage Permission Required'),
          content: const Text(
            'This app needs access to your storage to load and play video files. Please grant storage permission to continue.',
          ),
          actions: [
            TextButton(
              onPressed: () {
                Navigator.of(context).pop();
                openAppSettings(); // Open app settings to enable permission
              },
              child: const Text('Settings'),
            ),
            TextButton(
              onPressed: () {
                Navigator.of(context).pop();
              },
              child: const Text('Cancel'),
            ),
          ],
        );
      },
    );
  }

  Future<void> _loadVideos() async {
    setState(() {
      _isLoading = true;
    });

    try {
      final videos = await _videoService.getAllVideos();
      setState(() {
        _allVideos = videos;
        _isLoading = false;
      });
    } catch (e) {
      print('Error loading videos: $e');
      setState(() {
        _isLoading = false;
      });
    }
  }

  Future<void> _loadLastWatchedVideo() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final lastWatchedPath = prefs.getString('last_watched_file_name');
      
      if (lastWatchedPath != null && _allVideos.isNotEmpty) {
        final index = _allVideos.indexWhere((video) => video.path == lastWatchedPath);
        if (index != -1) {
          setState(() {
            _currentIndex = index;
          });
          
          // Scroll to the last watched video
          WidgetsBinding.instance.addPostFrameCallback((_) {
            _pageController.animateToPage(
              _currentIndex,
              duration: const Duration(milliseconds: 300),
              curve: Curves.easeInOut,
            );
          });
        }
      }
    } catch (e) {
      print('Error loading last watched video: $e');
    }
  }

  Future<void> _saveLastWatchedVideo(VideoModel video) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('last_watched_file_name', video.path);
    } catch (e) {
      print('Error saving last watched video: $e');
    }
  }

  void _onPageChanged(int index) {
    if (index < 0 || index >= _allVideos.length) return;
    
    setState(() {
      _currentIndex = index;
    });
    
    _saveLastWatchedVideo(_allVideos[index]);
  }

  void _onVideoEnd() {
    // Auto-advance to next video
    if (_currentIndex < _allVideos.length - 1) {
      _pageController.nextPage(
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeInOut,
      );
    }
  }

  @override
  void dispose() {
    _pageController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_hasPermission) {
      return Scaffold(
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(
                Icons.folder_off,
                size: 100,
                color: Colors.grey,
              ),
              const SizedBox(height: 16),
              const Text(
                'Storage Permission Required',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              const Text(
                'Please grant storage permission to load videos',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 24),
              ElevatedButton(
                onPressed: _checkPermissionAndLoadVideos,
                child: const Text('Grant Permission'),
              ),
            ],
          ),
        ),
      );
    }

    if (_isLoading) {
      return Scaffold(
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const CircularProgressIndicator(),
              const SizedBox(height: 16),
              Text(
                'Loading videos from ${_videoService.getVideoDirectory()}',
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      );
    }

    if (_allVideos.isEmpty) {
      return Scaffold(
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(
                Icons.video_library_outlined,
                size: 100,
                color: Colors.grey,
              ),
              const SizedBox(height: 16),
              const Text(
                'No Videos Found',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 8),
              const Text(
                'Please add videos to the BrokeBinge folder',
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 24),
              ElevatedButton(
                onPressed: _loadVideos,
                child: const Text('Refresh'),
              ),
            ],
          ),
        ),
      );
    }

    return Scaffold(
      body: PreloadPageView.builder(
        controller: _pageController,
        scrollDirection: Axis.vertical,
        itemCount: _allVideos.length,
        onPageChanged: _onPageChanged,
        preloadPagesCount: 2, // Preload next and previous pages
        itemBuilder: (context, index) {
          final video = _allVideos[index];
          final isCurrentPage = index == _currentIndex;
          
          return VideoPlayerWidget(
            video: video,
            autoPlay: isCurrentPage,
            onVideoEnd: isCurrentPage ? _onVideoEnd : null,
          );
        },
      ),
    );
  }
}