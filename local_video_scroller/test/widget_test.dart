import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('MaterialApp smoke test', (WidgetTester tester) async {
    // Harness check only — do not pump VideoFeedScreen (needs platform channels).
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: Text('smoke'),
        ),
      ),
    );

    expect(find.text('smoke'), findsOneWidget);
  });
}
