import 'opportunity.dart';

/// Why a boost in the plan is used alone: its best pairing, for comparison.
class PlanAlternative {
  const PlanAlternative({
    required this.partner,
    required this.togetherProfit,
    required this.separateProfit,
  });

  final GroupBoost partner;
  final double togetherProfit; // the two boosts on one hedge
  final double separateProfit; // the two boosts each used alone

  factory PlanAlternative.fromMap(Map<String, dynamic> data) => PlanAlternative(
        partner: GroupBoost.fromMap(
            Map<String, dynamic>.from(data['partner'] as Map)),
        togetherProfit: (data['togetherProfit'] as num? ?? 0).toDouble(),
        separateProfit: (data['separateProfit'] as num? ?? 0).toDouble(),
      );
}

/// One bet to place as part of the best plan.
class PlanStep {
  const PlanStep({
    required this.isTwoWay,
    required this.groupId,
    required this.boosts,
    required this.bet,
    required this.profit,
    required this.separateProfit,
    required this.alternative,
  });

  final bool isTwoWay;
  final String groupId;
  final List<GroupBoost> boosts;
  final Opportunity bet;
  final double profit;

  /// Two-way steps: what the same two boosts would earn used separately.
  final double? separateProfit;

  /// One-way steps: the best pairing that was passed over, if any.
  final PlanAlternative? alternative;

  factory PlanStep.fromMap(Map<String, dynamic> data) => PlanStep(
        isTwoWay: data['type'] == 'two_way',
        groupId: data['groupId'] as String? ?? '',
        boosts: [
          for (final b in (data['boosts'] as List? ?? const []))
            GroupBoost.fromMap(Map<String, dynamic>.from(b as Map)),
        ],
        bet: Opportunity.fromMap(Map<String, dynamic>.from(data['bet'] as Map)),
        profit: (data['profit'] as num? ?? 0).toDouble(),
        separateProfit: (data['separateProfit'] as num?)?.toDouble(),
        alternative: data['alternative'] == null
            ? null
            : PlanAlternative.fromMap(
                Map<String, dynamic>.from(data['alternative'] as Map)),
      );
}

/// The most profitable way to use every unused, active boost, computed by
/// the Cloud Function (each boost used once: alone or paired).
class BestPlan {
  const BestPlan({
    required this.totalProfit,
    required this.allSeparateProfit,
    required this.steps,
    required this.idleBoosts,
  });

  final double totalProfit;
  final double allSeparateProfit; // if every boost were used alone
  final List<PlanStep> steps;
  final List<GroupBoost> idleBoosts; // active boosts with no profitable hedge

  Set<String> get groupIds => {for (final s in steps) s.groupId};

  factory BestPlan.fromMap(Map<String, dynamic> data) => BestPlan(
        totalProfit: (data['totalProfit'] as num? ?? 0).toDouble(),
        allSeparateProfit: (data['allSeparateProfit'] as num? ?? 0).toDouble(),
        steps: [
          for (final s in (data['steps'] as List? ?? const []))
            PlanStep.fromMap(Map<String, dynamic>.from(s as Map)),
        ],
        idleBoosts: [
          for (final b in (data['idleBoosts'] as List? ?? const []))
            GroupBoost.fromMap(Map<String, dynamic>.from(b as Map)),
        ],
      );
}
