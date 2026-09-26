# FleetLink mobile foundation

The Dart application contains only a running indicator, Material 3 light/dark themes and English bootstrap copy. Flutter SDK localization delegates are used; there are no third-party mobile packages.
app owns composition and future Riverpod ProviderScope/GoRouter integration. Those packages are deliberately not installed until state/navigation behavior requires them. core holds future technical adapters, design_system owns themes, and features contains documentation only.

## Tooling status and setup
Flutter and Dart were unavailable in the FL-002 execution environment. The Dart sources, pubspec, widget tests and minimal web runner are provided; analyzer, widget tests, native builds and SDK-generated platform files are not claimed as verified.
Install a current stable SDK from the [official archive](https://docs.flutter.dev/install/archive), record its exact flutter --version output, then from this directory run:

```sh
flutter pub get
flutter analyze
flutter test
flutter run -d chrome
```

Commit the generated pubspec.lock after dependency resolution and review. It is intentionally not ignored or fabricated; reproducible mobile dependency resolution remains pending an available Flutter SDK. There is no mobile CI job pretending to validate an unresolved lockfile.

## Native runner generation
Android/iOS runners must be generated with the chosen stable SDK and reviewed before a native build. From this directory, with a clean checkout:
```sh
flutter create --project-name fleetlink --org example.fleetlink --platforms=android,ios .
flutter pub get
flutter analyze
flutter test
```
Review the generator diff and preserve the FleetLink source/tests, pubspec and documentation. Set platform display names to FleetLink. The provisional namespace example.fleetlink yields example.fleetlink.fleetlink identifiers; example is reserved for placeholders, not a claim to a corporate domain or approved release identifier. Final identifiers and signing remain unapproved. No signing credentials belong in Git.

Native runner generation and SDK pin/lock validation are explicit environment-limited follow-up work, not implemented product features. Replace English constants with generated ARB localization when locales/product copy are approved.
