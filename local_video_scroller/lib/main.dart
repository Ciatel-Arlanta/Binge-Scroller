import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'providers/video_state_provider.dart'; // Import your new provider
import 'screens/video_feed_screen.dart';

void main() {
  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => VideoStateProvider()),
      ],
      child: const MyApp(),
    ),
  );
}

class MyApp extends StatelessWidget {
  const MyApp({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Local Video Scroller',
      theme: ThemeData.dark(), // Dark theme usually looks better for video apps
      home: const VideoFeedScreen(),
    );
  }
}