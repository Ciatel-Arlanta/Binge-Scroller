import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:preload_page_view/preload_page_view.dart';
import 'package:permission_handler/permission_handler.dart';
import '../providers/video_state_provider.dart';
import '../widgets/video_player_widget.dart';

class VideoFeedScreen extends StatefulWidget {
  const VideoFeedScreen({Key? key}) : super(key: key);

  @override
  State<VideoFeedScreen> createState() => _VideoFeedScreenState();
}

class _VideoFeedScreenState extends State<VideoFeedScreen> with WidgetsBindingObserver {
  PreloadPageController? _pageController;
  bool _hasPermission = false;
  bool _isAppInBackground = false;

  @override
  void initState() {
    super.initState();
    _checkPermissions();
    // Add page visibility listener
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _pageController?.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    super.didChangeAppLifecycleState(state);
    final videoState = Provider.of<VideoStateProvider>(context, listen: false);
    
    switch (state) {
      case AppLifecycleState.paused:
        _isAppInBackground = true;
        // Pause all videos when app goes to background
        // This will be handled by the VideoPlayerWidget's didUpdateWidget method
        // We temporarily set a flag to indicate app is in background
        break;
      case AppLifecycleState.resumed:
        _isAppInBackground = false;
        // Resume current video when app comes to foreground
        // Trigger a rebuild to update the video player widget
        setState(() {});
        break;
      case AppLifecycleState.detached:
      case AppLifecycleState.inactive:
      case AppLifecycleState.hidden:
        break;
    }
  }

  Future<void> _checkPermissions() async {
    var status = await Permission.videos.request();
    if (status.isDenied) {
      status = await Permission.storage.request();
    }
    if (status.isGranted) {
      setState(() {
        _hasPermission = true;
      });
    } else {
      _showPermissionDeniedDialog();
    }
  }

  void _showPermissionDeniedDialog() {
    showDialog(
      context: context,
      builder: (BuildContext context) {
        return AlertDialog(
          title: const Text('Permission Required'),
          content: const Text(
            'This app needs access to your videos to function. Please grant the permission in settings.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('OK'),
            ),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    if (!_hasPermission) {
      return Scaffold(
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.folder_off, size: 100, color: Colors.grey),
              const SizedBox(height: 16),
              const Text(
                'Storage Permission Required',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 24),
              ElevatedButton(
                onPressed: _checkPermissions,
                child: const Text('Grant Permission'),
              ),
            ],
          ),
        ),
      );
    }

    return Consumer<VideoStateProvider>(
      builder: (context, videoState, child) {
        if (videoState.isLoading) {
          return const Scaffold(
            backgroundColor: Colors.black,
            body: Center(child: CircularProgressIndicator()),
          );
        }

        if (videoState.videos.isEmpty) {
          return Scaffold(
            backgroundColor: Colors.black,
            body: Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: const [
                  Icon(Icons.videocam_off, size: 64, color: Colors.grey),
                  SizedBox(height: 16),
                  Text(
                    "No videos found in BrokeBinge folder",
                    style: TextStyle(color: Colors.white),
                  ),
                ],
              ),
            ),
          );
        }

        _pageController ??= PreloadPageController(
          initialPage: videoState.currentIndex,
          viewportFraction: 1.0,
        );

        return Scaffold(
          backgroundColor: Colors.black,
          body: PreloadPageView.builder(
            controller: _pageController,
            scrollDirection: Axis.vertical,
            itemCount: videoState.videos.length,
            preloadPagesCount: 2,
            onPageChanged: (index) {
              videoState.updateIndex(index);
            },
            itemBuilder: (context, index) {
              final video = videoState.videos[index];
              final isCurrent = index == videoState.currentIndex;
              // Only play if it's the current video AND app is not in background
              final shouldAutoPlay = isCurrent && !_isAppInBackground;
              
              return VideoPlayerWidget(
                video: video,
                autoPlay: shouldAutoPlay,
                onVideoEnd: () {
                  if (index < videoState.videos.length - 1) {
                    _pageController?.nextPage(
                      duration: const Duration(milliseconds: 300),
                      curve: Curves.easeInOut,
                    );
                  }
                },
              );
            },
          ),
        );
      },
    );
  }
}