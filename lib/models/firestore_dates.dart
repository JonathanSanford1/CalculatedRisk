import 'package:cloud_firestore/cloud_firestore.dart';

/// Reads a date stored as a Firestore Timestamp (current format) or an ISO
/// string (early test data) and returns it in the phone's local time.
DateTime readDate(Object? value) {
  if (value is Timestamp) return value.toDate();
  if (value is String) {
    return DateTime.tryParse(value)?.toLocal() ?? DateTime.now();
  }
  return DateTime.now();
}
