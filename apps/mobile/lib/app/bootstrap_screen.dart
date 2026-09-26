import 'package:flutter/material.dart';

import 'l10n/bootstrap_strings.dart';

class BootstrapScreen extends StatelessWidget {
  const BootstrapScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text(BootstrapStrings.appName)),
      body: const SafeArea(
        child: Center(
          child: Padding(
            padding: EdgeInsets.all(24),
            child: Text(
              BootstrapStrings.running,
              textAlign: TextAlign.center,
            ),
          ),
        ),
      ),
    );
  }
}
