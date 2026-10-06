import 'package:flutter/material.dart';

import '../models/best_plan.dart';
import '../models/hedge_goal.dart';
import '../models/opportunity.dart';
import '../utils/format.dart';
import 'opportunity_card.dart';

/// The best way to use every unused boost whose window is open: which to
/// pair, which to use alone, and how that compares with the alternatives.
class BestPlanCard extends StatelessWidget {
  const BestPlanCard({super.key, required this.plan, required this.onPlaced});

  final BestPlan plan;
  final void Function(Opportunity bet, HedgeVersion version) onPlaced;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final byGuaranteed = plan.goal == HedgeGoal.guaranteed;
    final boostCount = plan.steps.fold<int>(0, (sum, s) => sum + s.boosts.length);
    final betCount = plan.steps.length;
    final gain = plan.totalProfit - plan.allSeparateProfit;
    final measure = byGuaranteed ? 'guaranteed profit' : 'possible profit';

    final summary = StringBuffer(
      'Place ${betCount == 1 ? 'this bet' : 'these $betCount bets'} to use '
      '$boostCount ${boostCount == 1 ? 'boost' : 'boosts'}. ',
    );
    if (gain > 0.005) {
      summary.write('That\'s ${formatCents(gain)} more $measure than using every '
          'boost on its own (${formatCents(plan.allSeparateProfit)}).');
    } else if (boostCount > 1) {
      summary.write('Right now, using each boost on its own earns the most.');
    }

    return Card(
      margin: const EdgeInsets.fromLTRB(12, 6, 12, 10),
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: Colors.indigo.shade300, width: 1.5),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            color: Colors.indigo.shade50,
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.insights, color: Colors.indigo.shade800),
                    const SizedBox(width: 8),
                    Text(
                      byGuaranteed ? 'Best plan: most guaranteed' : 'Best plan: highest upside',
                      style: theme.textTheme.titleMedium?.copyWith(
                        fontWeight: FontWeight.bold,
                        color: Colors.indigo.shade900,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  byGuaranteed
                      ? '+${formatCents(plan.totalGuaranteed)} guaranteed'
                      : 'Up to +${formatCents(plan.totalMax)}',
                  style: theme.textTheme.headlineSmall?.copyWith(
                    fontWeight: FontWeight.bold,
                    color: byGuaranteed ? Colors.green.shade800 : Colors.indigo.shade700,
                  ),
                ),
                Text(
                  byGuaranteed
                      ? 'Up to +${formatCents(plan.totalMax)} if the higher-paying bets win.'
                      : 'At least +${formatCents(plan.totalGuaranteed)} guaranteed whatever happens.',
                  style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 4),
                Text(summary.toString().trim(), style: theme.textTheme.bodyMedium),
              ],
            ),
          ),
          for (var i = 0; i < plan.steps.length; i++) ...[
            if (i > 0) const Divider(height: 1, thickness: 1),
            _StepView(
              step: plan.steps[i],
              number: i + 1,
              goal: plan.goal,
              onPlaced: (version) => onPlaced(plan.steps[i].bet, version),
            ),
          ],
          if (plan.idleBoosts.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 14),
              child: Text(
                'No profitable hedge right now for: '
                '${plan.idleBoosts.map((b) => '${b.sportsbook.label} ${b.title}').join(', ')}.',
                style: theme.textTheme.bodySmall,
              ),
            ),
        ],
      ),
    );
  }
}

class _StepView extends StatelessWidget {
  const _StepView({
    required this.step,
    required this.number,
    required this.goal,
    required this.onPlaced,
  });

  final PlanStep step;
  final int number;
  final HedgeGoal goal;
  final void Function(HedgeVersion version) onPlaced;

  String _comparison() {
    final measure = goal == HedgeGoal.guaranteed ? 'guaranteed' : 'possible';
    final value = formatCents(step.profit);
    final separate = step.separateProfit;
    if (step.isTwoWay && separate != null) {
      return 'Together: +$value $measure. Used separately, these boosts would '
          'reach +${formatCents(separate)}, so pairing them adds '
          '${formatCents(step.profit - separate)}.';
    }
    final alt = step.alternative;
    if (alt != null) {
      return 'Alone: +$value $measure. Its best pairing, with '
          '${alt.partner.sportsbook.label} ${alt.partner.title}, would reach '
          '+${formatCents(alt.togetherProfit)} together vs '
          '+${formatCents(alt.separateProfit)} with both used on their own.';
    }
    return 'Alone: +$value $measure. No pairing beats using it alone.';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  CircleAvatar(
                    radius: 12,
                    backgroundColor: Colors.indigo.shade700,
                    child: Text('$number',
                        style: const TextStyle(
                            color: Colors.white, fontSize: 12, fontWeight: FontWeight.bold)),
                  ),
                  const SizedBox(width: 10),
                  Text(step.isTwoWay ? 'Two boosts together' : 'One boost alone',
                      style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.bold)),
                ],
              ),
              const SizedBox(height: 8),
              for (final boost in step.boosts) BoostLine(boost: boost),
              const SizedBox(height: 6),
              Text(_comparison(),
                  style: theme.textTheme.bodySmall?.copyWith(color: Colors.indigo.shade900)),
            ],
          ),
        ),
        OpportunityDetails(
          key: ValueKey('plan-${step.bet.id}-${goal.name}'),
          opportunity: step.bet,
          goal: goal,
          initialVersionKey: step.versionKey,
          onPlaced: onPlaced,
        ),
      ],
    );
  }
}
