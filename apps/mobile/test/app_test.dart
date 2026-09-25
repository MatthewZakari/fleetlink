import 'package:fleetlink/app/app.dart';
import 'package:fleetlink/design_system/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('application boots with an accessible running message', (tester) async {
    await tester.pumpWidget(const FleetLinkApp());
    expect(find.text('FleetLink'), findsOneWidget);
    expect(find.text('FleetLink is running.'), findsOneWidget);
    final app = tester.widget<MaterialApp>(find.byType(MaterialApp));
    expect(app.themeMode, ThemeMode.system);
    expect(app.supportedLocales, contains(const Locale('en')));
    expect(tester.takeException(), isNull);
  });

  test('both themes enable Material 3 with distinct brightness', () {
    expect(FleetLinkTheme.light.useMaterial3, isTrue);
    expect(FleetLinkTheme.dark.useMaterial3, isTrue);
    expect(FleetLinkTheme.light.brightness, Brightness.light);
    expect(FleetLinkTheme.dark.brightness, Brightness.dark);
  });

  testWidgets('bootstrap renders in dark mode with large text', (tester) async {
    tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
    tester.platformDispatcher.textScaleFactorTestValue = 2;
    addTearDown(tester.platformDispatcher.clearAllTestValues);
    await tester.pumpWidget(const FleetLinkApp());
    await tester.pumpAndSettle();
    final context = tester.element(find.text('FleetLink is running.'));
    expect(Theme.of(context).brightness, Brightness.dark);
    expect(tester.takeException(), isNull);
  });
}
