import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import '../models/video_model.dart';

class VideoService {
  static const String _defaultDirectory = 'BrokeBinge';

  Future<Directory> getVideoDirectory() async {
    if (Platform.isAndroid) {
      // App-specific Movies path looks like:
      // /storage/emulated/0/Android/data/<pkg>/files/Movies
      // Strip from /Android/ onward to reach the shared primary volume,
      // then use shared Movies/BrokeBinge (matches README / USB workflow).
      final dirs =
          await getExternalStorageDirectories(type: StorageDirectory.movies);
      if (dirs != null && dirs.isNotEmpty) {
        final appPath = dirs.first.path;
        // Shared primary volume is everything before the app-specific Android/ segment.
        if (appPath.contains('/Android/')) {
          final root = appPath.split('/Android/').first; // e.g. /storage/emulated/0
          final targetDir = Directory('$root/Movies/$_defaultDirectory');
          if (!await targetDir.exists()) {
            try {
              await targetDir.create(recursive: true);
            } catch (_) {
              // May fail without write permission; listing still works if
              // the user created the folder via USB.
            }
          }
          debugPrint('Looking for videos in: ${targetDir.path}');
          return targetDir;
        }
        debugPrint(
          'Unexpected external storage path (no /Android/ segment): $appPath',
        );
      }
    }

    // Fallback for other platforms
    final appDir = await getApplicationDocumentsDirectory();
    final targetDir = Directory('${appDir.path}/$_defaultDirectory');

    if (!await targetDir.exists()) {
      await targetDir.create(recursive: true);
    }

    return targetDir;
  }

  Future<List<VideoModel>> getAllVideos() async {
    try {
      final directory = await getVideoDirectory();

      if (!await directory.exists()) {
        debugPrint('Error: Directory ${directory.path} does not exist.');
        return [];
      }

      final files = await directory
          .list()
          .where(
            (entity) =>
                entity is File &&
                entity.path.toLowerCase().endsWith('.mp4'),
          )
          .cast<File>()
          .toList();

      debugPrint('Found ${files.length} .mp4 files.');

      files.sort((a, b) => a.path.compareTo(b.path));

      return files.map((file) => VideoModel.fromPath(file.path)).toList();
    } catch (e) {
      debugPrint('Error getting videos: $e');
      return [];
    }
  }

  Future<Map<String, List<VideoModel>>> getVideosByEpisode() async {
    final allVideos = await getAllVideos();
    final Map<String, List<VideoModel>> episodeMap = {};

    for (final video in allVideos) {
      final episodeKey =
          '${video.showName}_S${video.season.toString().padLeft(2, '0')}E${video.episode.toString().padLeft(2, '0')}';

      if (!episodeMap.containsKey(episodeKey)) {
        episodeMap[episodeKey] = [];
      }

      episodeMap[episodeKey]!.add(video);
    }

    episodeMap.forEach((key, videos) {
      videos.sort((a, b) => a.part.compareTo(b.part));
    });

    return episodeMap;
  }
}
