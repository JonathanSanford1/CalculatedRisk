import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:cloud_functions/cloud_functions.dart';

import '../models/best_plan.dart';
import '../models/opportunity.dart';
import '../models/profit_boost.dart';
import '../models/rounding.dart';

/// Everything the app reads from or sends to Firebase for one user.
///
///   users/{uid}/boosts          the app writes these
///   users/{uid}/hedge_groups    the Cloud Function writes these
///   users/{uid}/meta/status     the Cloud Function writes this
///   users/{uid}/meta/plan       the Cloud Function writes this (best plan)
///   users/{uid}/settings/preferences   the app writes this (rounding mode)
class BoostRepository {
  BoostRepository({required String uid})
      : _user = FirebaseFirestore.instance.collection('users').doc(uid);

  final DocumentReference<Map<String, dynamic>> _user;

  CollectionReference<Map<String, dynamic>> get _boosts =>
      _user.collection('boosts');

  // Boosts

  Stream<List<ProfitBoost>> watchBoosts() {
    return _boosts.snapshots().map(
          (snapshot) => snapshot.docs
              .map((doc) => ProfitBoost.fromFirestore(doc.id, doc.data()))
              .toList(),
        );
  }

  /// Creates or updates a boost. Either one triggers a fresh hedge check.
  Future<void> saveBoost(ProfitBoost boost, {required bool isNew}) =>
      _boosts.doc(boost.id).set(
            boost.toFirestore(isNew: isNew),
            SetOptions(merge: true),
          );

  Future<void> deleteBoost(String id) => _boosts.doc(id).delete();

  // Marking boosts used

  /// Records that [bet] was placed: every boost it uses (one, or two for a
  /// two-way hedge) is marked used, with the hedge saved on it.
  Future<void> markPlaced(Opportunity bet) {
    final batch = FirebaseFirestore.instance.batch();
    for (final id in bet.boostIds) {
      batch.update(_boosts.doc(id), {
        'used': true,
        'usedAt': FieldValue.serverTimestamp(),
        'placedBet': bet.raw,
      });
    }
    return batch.commit();
  }

  /// Marks a boost used without recording which hedge was placed.
  Future<void> markUsed(String boostId) => _boosts.doc(boostId).update({
        'used': true,
        'usedAt': FieldValue.serverTimestamp(),
        'placedBet': null,
      });

  /// Makes boosts available again and clears any recorded hedge.
  Future<void> markUnused(Iterable<String> boostIds) {
    final batch = FirebaseFirestore.instance.batch();
    for (final id in boostIds) {
      batch.update(_boosts.doc(id), {
        'used': false,
        'usedAt': null,
        'placedBet': null,
      });
    }
    return batch.commit();
  }

  // Hedges found by the Cloud Function

  Stream<List<HedgeGroup>> watchHedgeGroups() {
    return _user
        .collection('hedge_groups')
        .orderBy('bestProfit', descending: true)
        .snapshots()
        .map(
          (snapshot) => snapshot.docs
              .map((doc) => HedgeGroup.fromFirestore(doc.id, doc.data()))
              .toList(),
        );
  }

  /// The current hedge cards, read once (for choosing which hedge was placed).
  Future<List<HedgeGroup>> fetchHedgeGroups() async {
    final snapshot = await _user.collection('hedge_groups').get();
    return snapshot.docs
        .map((doc) => HedgeGroup.fromFirestore(doc.id, doc.data()))
        .toList();
  }

  /// The best plan for every rounding mode.
  Stream<BestPlanSet?> watchPlans() {
    return _user.collection('meta').doc('plan').snapshots().map((snapshot) {
      final data = snapshot.data();
      return data == null ? null : BestPlanSet.fromMap(data);
    });
  }

  // Rounding preference

  DocumentReference<Map<String, dynamic>> get _preferences =>
      _user.collection('settings').doc('preferences');

  Stream<RoundingMode> watchRoundingMode() => _preferences
      .snapshots()
      .map((snapshot) => RoundingMode.fromName(snapshot.data()?['roundingMode']));

  Future<RoundingMode> fetchRoundingMode() async {
    try {
      final snapshot = await _preferences.get();
      return RoundingMode.fromName(snapshot.data()?['roundingMode']);
    } catch (_) {
      return RoundingMode.none;
    }
  }

  /// Saves the selection (the Cloud Function also uses it for its status
  /// message). Switching is instant: hedges exist for every mode already.
  Future<void> setRoundingMode(RoundingMode mode) => _preferences.set(
        {'roundingMode': mode.name},
        SetOptions(merge: true),
      );

  Stream<RefreshStatus?> watchStatus() {
    return _user.collection('meta').doc('status').snapshots().map((snapshot) {
      final data = snapshot.data();
      return data == null ? null : RefreshStatus.fromMap(data);
    });
  }

  /// Asks the Cloud Function to re-check odds now. Returns its summary message.
  Future<String> refreshNow() async {
    final callable = FirebaseFunctions.instance.httpsCallable(
      'refresh_opportunities',
      options: HttpsCallableOptions(timeout: const Duration(minutes: 2)),
    );
    final result = await callable.call();
    final data = Map<String, dynamic>.from(result.data as Map);
    return data['message'] as String? ?? 'Hedges updated.';
  }
}
