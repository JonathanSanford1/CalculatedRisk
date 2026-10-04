String formatOdds(int odds) => odds > 0 ? '+$odds' : '$odds';

String formatPercent(double value) => value == value.roundToDouble()
    ? '${value.toStringAsFixed(0)}%'
    : '${value.toStringAsFixed(1)}%';

/// "$50" for whole dollars, "$37.50" otherwise.
String formatMoney(double value) => value == value.roundToDouble()
    ? '\$${value.toStringAsFixed(0)}'
    : '\$${value.toStringAsFixed(2)}';

/// Always shows cents: "$4.65".
String formatCents(double value) => '\$${value.toStringAsFixed(2)}';

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

/// e.g. "just now", "12 min ago", "3 hr ago", then a full date.
String formatAgo(DateTime time) {
  final diff = DateTime.now().difference(time);
  if (diff.inMinutes < 1) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes} min ago';
  if (diff.inHours < 24) return '${diff.inHours} hr ago';
  return formatDateTime(time);
}
