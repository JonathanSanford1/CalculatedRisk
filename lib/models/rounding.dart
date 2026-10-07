/// How bet amounts are rounded. Must match ROUNDING_MODES in functions/main.py.
enum RoundingMode {
  small('Small', '50¢ steps under \$10, \$1 steps from \$10 to \$25, \$2.50 steps from \$25 to \$50, \$5 steps over \$50.'),
  medium('Medium', '\$1 steps under \$10, \$2.50 steps from \$10 to \$25, \$5 steps from \$25 to \$50, \$10 steps over \$50.'),
  large('Large', '\$1 steps under \$10, \$5 steps from \$10 to \$25, \$10 steps from \$25 to \$50, \$25 steps over \$50.');

  const RoundingMode(this.label, this.description);
  final String label;
  final String description;

  /// Values older versions of the app saved. "none" is gone; small replaces it.
  static const Map<String, RoundingMode> _legacy = {
    'none': small,
    'light': medium,
    'heavy': large,
  };

  static RoundingMode fromName(Object? name) =>
      RoundingMode.values.asNameMap()[name] ?? _legacy[name] ?? RoundingMode.small;
}
