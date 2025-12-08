import 'package:flutter/material.dart';
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

class _VideoFeedScreenState extends State<VideoFeedScreen> {
  // We don't initialize the controller in initState anymore.
  // We initialize it only after we know the start index from the provider.
  PreloadPageController? _pageController;
  bool _hasPermission = false;

  @override
  void initState() {
    super.initState();
    _checkPermissions();
  }

  Future<void> _checkPermissions() async {
    // On Android 13+, we request the specific 'videos' permission.
    var status = await Permission.videos.request();

    // For older Androids, fallback to storage.
    if (status.isDenied) {
      status = await Permission.storage.request();
    }

    if (status.isGranted) {
      setState(() {
        _hasPermission = true;
      });
      // Permission granted, the Provider is likely already loading in background
      // because it was initialized in main.dart.
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
  void dispose() {
    _pageController?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // 1. If permission is denied, show the placeholder UI
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

    // 2. Use Consumer to listen to the VideoStateProvider
    return Consumer<VideoStateProvider>(
      builder: (context, videoState, child) {
        // A. Show Loading Indicator while fetching files/prefs
        if (videoState.isLoading) {
          return const Scaffold(
            backgroundColor: Colors.black,
            body: Center(child: CircularProgressIndicator()),
          );
        }

        // B. Show Empty State if no videos found
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

        // C. Initialize Controller ONCE with the saved index
        // The ??= operator ensures we only create it if it's currently null
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
            preloadPagesCount: 2, // Keeps prev/next video ready in memory
            onPageChanged: (index) {
              // Save the new index to state/prefs via Provider
              videoState.updateIndex(index);
            },
            itemBuilder: (context, index) {
              final video = videoState.videos[index];
              // Check against the Provider's index for auto-play logic
              final isCurrent = index == videoState.currentIndex;

              return VideoPlayerWidget(
                video: video,
                autoPlay: isCurrent,
                onVideoEnd: () {
                  // Auto-scroll logic
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
