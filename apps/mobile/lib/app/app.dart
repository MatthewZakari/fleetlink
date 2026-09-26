import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import '../design_system/theme.dart';
import 'bootstrap_screen.dart';
import 'l10n/bootstrap_strings.dart';

class FleetLinkApp extends StatelessWidget {
  const FleetLinkApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: BootstrapStrings.appName,
      debugShowCheckedModeBanner: false,
      theme: FleetLinkTheme.light,
      darkTheme: FleetLinkTheme.dark,
      themeMode: ThemeMode.system,
      supportedLocales: const [Locale('en')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      home: const BootstrapScreen(),
    );
  }
}
