String formatOdds(int odds) => odds > 0 ? '+$odds' : '$odds';

String formatPercent(double value) => value == value.roundToDouble()
    ? '${value.toStringAsFixed(0)}%'
    : '${value.toStringAsFixed(1)}%';

String formatMoney(double value) => value == value.roundToDouble()
    ? '\$${value.toStringAsFixed(0)}'
    : '\$${value.toStringAsFixed(2)}';

const _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/// e.g. "Oct 3, 7:05 PM"
String formatDateTime(DateTime dt) {
  final hour12 = dt.hour % 12 == 0 ? 12 : dt.hour % 12;
  final minute = dt.minute.toString().padLeft(2, '0');
  final amPm = dt.hour < 12 ? 'AM' : 'PM';
  return '${_months[dt.month - 1]} ${dt.day}, $hour12:$minute $amPm';
}