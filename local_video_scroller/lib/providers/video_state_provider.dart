import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/video_model.dart';
import '../services/video_service.dart';

class VideoStateProvider extends ChangeNotifier {
  final VideoService _videoService = VideoService();
  
  List<VideoModel> _videos = [];
  int _currentIndex = 0;
  bool _isLoading = true;

  // Getters
  List<VideoModel> get videos => _videos;
  int get currentIndex => _currentIndex;
  bool get isLoading => _isLoading;

  VideoStateProvider() {
    _init();
  }

  Future<void> _init() async {
    _isLoading = true;
    notifyListeners();

    // 1. Load all videos from storage
    _videos = await _videoService.getAllVideos();

    // 2. Load the last watched path from SharedPreferences
    final prefs = await SharedPreferences.getInstance();
    final lastPath = prefs.getString('last_watched_path');

    // 3. Find the index of that video
    if (lastPath != null && _videos.isNotEmpty) {
      final index = _videos.indexWhere((v) => v.path == lastPath);
      if (index != -1) {
        _currentIndex = index;
      }
    }

    _isLoading = false;
    notifyListeners();
  }

  // Call this whenever the page changes
  Future<void> updateIndex(int index) async {
    _currentIndex = index;
    // notifyListeners(); // Not strictly needed here if just scrolling, avoids rebuild loops
    
    if (_videos.isNotEmpty && index >= 0 && index < _videos.length) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('last_watched_path', _videos[index].path);
    }
  }
}