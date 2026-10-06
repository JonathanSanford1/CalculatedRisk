import 'hedge_goal.dart';
import 'opportunity.dart';
import 'rounding.dart';

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
    required this.versionKey,
    required this.profit,
    required this.guaranteedProfit,
    required this.maxProfit,
    required this.separateProfit,
    required this.alternative,
  });

  final bool isTwoWay;
  final String groupId;
  final List<GroupBoost> boosts;
  final Opportunity bet;
  final String versionKey; // which stake version the plan uses
  final double profit; // the value the plan ranked by (guaranteed or upside)
  final double guaranteedProfit; // of that version
  final double maxProfit; // of that version

  /// Two-boost steps: what the same two boosts would earn used separately.
  final double? separateProfit;

  /// One-boost steps: the best pairing that was passed over, if any.
  final PlanAlternative? alternative;

  factory PlanStep.fromMap(Map<String, dynamic> data) {
    final bet = Opportunity.fromMap(Map<String, dynamic>.from(data['bet'] as Map));
    final profit = (data['profit'] as num? ?? 0).toDouble();
    return PlanStep(
      isTwoWay: data['type'] == 'two_way',
      groupId: data['groupId'] as String? ?? '',
      boosts: [
        for (final b in (data['boosts'] as List? ?? const []))
          GroupBoost.fromMap(Map<String, dynamic>.from(b as Map)),
      ],
      bet: bet,
      versionKey: data['versionKey'] as String? ?? 'safest',
      profit: profit,
      guaranteedProfit: (data['guaranteedProfit'] as num? ?? profit).toDouble(),
      maxProfit: (data['maxProfit'] as num? ?? profit).toDouble(),
      separateProfit: (data['separateProfit'] as num?)?.toDouble(),
      alternative: data['alternative'] == null
          ? null
          : PlanAlternative.fromMap(
              Map<String, dynamic>.from(data['alternative'] as Map)),
    );
  }
}

/// The most profitable way to use every unused boost whose window is open,
/// for one goal (each boost used once: alone or paired).
class BestPlan {
  const BestPlan({
    required this.goal,
    required this.totalProfit,
    required this.allSeparateProfit,
    required this.steps,
    required this.idleBoosts,
  });

  final HedgeGoal goal;
  final double totalProfit; // sum of the steps' values for the goal
  final double allSeparateProfit; // if every boost were used alone
  final List<PlanStep> steps;
  final List<GroupBoost> idleBoosts; // open boosts with no profitable hedge

  Set<String> get groupIds => {for (final s in steps) s.groupId};
  double get totalGuaranteed =>
      steps.fold(0.0, (sum, s) => sum + s.guaranteedProfit);
  double get totalMax => steps.fold(0.0, (sum, s) => sum + s.maxProfit);

  factory BestPlan.fromMap(Map<String, dynamic> data) => BestPlan(
        goal: HedgeGoal.fromName(data['objective']),
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

/// The best plan for every rounding mode and goal.
class BestPlanSet {
  const BestPlanSet(this.plans);

  final Map<RoundingMode, Map<HedgeGoal, BestPlan>> plans;

  BestPlan? planFor(RoundingMode mode, HedgeGoal goal) => plans[mode]?[goal];

  factory BestPlanSet.fromMap(Map<String, dynamic> data) {
    final plans = <RoundingMode, Map<HedgeGoal, BestPlan>>{};
    final rawPlans = data['plans'];
    if (rawPlans is Map) {
      for (final modeEntry in rawPlans.entries) {
        final byGoal = <HedgeGoal, BestPlan>{};
        for (final goalEntry in (modeEntry.value as Map).entries) {
          byGoal[HedgeGoal.fromName(goalEntry.key)] = BestPlan.fromMap(
              Map<String, dynamic>.from(goalEntry.value as Map));
        }
        plans[RoundingMode.fromName(modeEntry.key)] = byGoal;
      }
    } else {
      // Saved before goals existed: guaranteed plans only.
      final rawModes = data['modes'];
      if (rawModes is Map) {
        for (final entry in rawModes.entries) {
          plans[RoundingMode.fromName(entry.key)] = {
            HedgeGoal.guaranteed: BestPlan.fromMap(
                Map<String, dynamic>.from(entry.value as Map)),
          };
        }
      } else {
        plans[RoundingMode.none] = {HedgeGoal.guaranteed: BestPlan.fromMap(data)};
      }
    }
    return BestPlanSet(plans);
  }
}
