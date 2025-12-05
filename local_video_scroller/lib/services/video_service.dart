import 'dart:io';
import 'package:path_provider/path_provider.dart';
import '../models/video_model.dart';

class VideoService {
  static const String _defaultDirectory = 'BrokeBinge';

  Future<Directory> getVideoDirectory() async {
    Directory? directory;
    
    if (Platform.isAndroid) {
      directory = await getExternalStorageDirectory();
      if (directory != null) {
        // Navigate up to the parent directory to access external storage
        final parentDir = directory.parent;
        final targetDir = Directory('${parentDir.path}/$_defaultDirectory');
        
        if (!await targetDir.exists()) {
          await targetDir.create(recursive: true);
        }
        
        return targetDir;
      }
    }
    
    // Fallback to app documents directory
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
      final files = await directory.list().where((entity) => 
          entity is File && entity.path.endsWith('.mp4')).cast<File>().toList();
      
      // Sort files by name to ensure proper order
      files.sort((a, b) => a.path.compareTo(b.path));
      
      return files.map((file) => VideoModel.fromPath(file.path)).toList();
    } catch (e) {
      print('Error getting videos: $e');
      return [];
    }
  }

  Future<Map<String, List<VideoModel>>> getVideosByEpisode() async {
    final allVideos = await getAllVideos();
    final Map<String, List<VideoModel>> episodeMap = {};
    
    for (final video in allVideos) {
      final episodeKey = '${video.showName}_S${video.season.toString().padLeft(2, '0')}E${video.episode.toString().padLeft(2, '0')}';
      
      if (!episodeMap.containsKey(episodeKey)) {
        episodeMap[episodeKey] = [];
      }
      
      episodeMap[episodeKey]!.add(video);
    }
    
    // Sort videos within each episode by part number
    episodeMap.forEach((key, videos) {
      videos.sort((a, b) => a.part.compareTo(b.part));
    });
    
    return episodeMap;
  }
}