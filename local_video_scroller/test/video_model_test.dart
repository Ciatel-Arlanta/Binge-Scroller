import 'package:flutter_test/flutter_test.dart';
import 'package:binge_scroller/models/video_model.dart';

void main() {
  group('VideoModel.fromPath', () {
    test('parses show name, season, episode, and part from filename', () {
      final model = VideoModel.fromPath(
        '/x/Attack_on_Titan_S01E02_Part003.mp4',
      );

      expect(model.showName, 'Attack on Titan');
      expect(model.season, 1);
      expect(model.episode, 2);
      expect(model.part, 3);
      expect(model.displayName, 'Attack on Titan S01E02 Part003');
      expect(model.path, '/x/Attack_on_Titan_S01E02_Part003.mp4');
    });

    test('falls back for non-matching filenames', () {
      final model = VideoModel.fromPath('/x/random_clip.mp4');

      expect(model.showName, 'random_clip');
      expect(model.season, 1);
      expect(model.episode, 1);
      expect(model.part, 1);
    });

    test('parses multi-digit season, episode, and part', () {
      final model = VideoModel.fromPath(
        '/x/Some_Show_S12E34_Part120.mp4',
      );

      expect(model.showName, 'Some Show');
      expect(model.season, 12);
      expect(model.episode, 34);
      expect(model.part, 120);
      expect(model.displayName, 'Some Show S12E34 Part120');
    });
  });

  group('VideoModel JSON', () {
    test('toJson/fromJson round-trip preserves all fields', () {
      final original = VideoModel(
        path: '/x/Attack_on_Titan_S01E02_Part003.mp4',
        showName: 'Attack on Titan',
        season: 1,
        episode: 2,
        part: 3,
      );

      final restored = VideoModel.fromJson(original.toJson());

      expect(restored.path, original.path);
      expect(restored.showName, original.showName);
      expect(restored.season, original.season);
      expect(restored.episode, original.episode);
      expect(restored.part, original.part);
      expect(restored.displayName, original.displayName);
    });
  });
}
