/// How bet amounts are rounded. Must match ROUNDING_MODES in functions/main.py.
enum RoundingMode {
  none('No rounding', 'Exact amounts: the boost\'s max, hedge to the nearest 50¢.'),
  light('Light', '50¢ steps under \$10, \$1 steps from \$10 to \$50, \$5 steps over \$50.'),
  heavy('Heavy', '\$1 steps under \$10, \$2 steps from \$10 to \$50, \$10 steps over \$50.');

  const RoundingMode(this.label, this.description);
  final String label;
  final String description;

  static RoundingMode fromName(Object? name) =>
      RoundingMode.values.asNameMap()[name] ?? RoundingMode.none;
}
