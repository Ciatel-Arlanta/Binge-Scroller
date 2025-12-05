class VideoModel {
  final String path;
  final String showName;
  final int season;
  final int episode;
  final int part;
  final String displayName;

  VideoModel({
    required this.path,
    required this.showName,
    required this.season,
    required this.episode,
    required this.part,
  }) : displayName = '$showName S${season.toString().padLeft(2, '0')}E${episode.toString().padLeft(2, '0')} Part${part.toString().padLeft(3, '0')}';

  factory VideoModel.fromPath(String path) {
    final fileName = path.split('/').last;
    final regex = RegExp(r'(.+?)_S(\d+)E(\d+)_Part(\d+)\.mp4');
    final match = regex.firstMatch(fileName);

    if (match != null) {
      return VideoModel(
        path: path,
        showName: match.group(1)!.replaceAll('_', ' '),
        season: int.parse(match.group(2)!),
        episode: int.parse(match.group(3)!),
        part: int.parse(match.group(4)!),
      );
    } else {
      // Fallback for files that don't match the expected pattern
      return VideoModel(
        path: path,
        showName: fileName.split('.').first,
        season: 1,
        episode: 1,
        part: 1,
      );
    }
  }

  Map<String, dynamic> toJson() {
    return {
      'path': path,
      'showName': showName,
      'season': season,
      'episode': episode,
      'part': part,
    };
  }

  factory VideoModel.fromJson(Map<String, dynamic> json) {
    return VideoModel(
      path: json['path'],
      showName: json['showName'],
      season: json['season'],
      episode: json['episode'],
      part: json['part'],
    );
  }
}