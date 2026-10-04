import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/opportunity.dart';
import '../utils/format.dart';
import 'sportsbook_style.dart';

/// One card per boost combination. Collapsed it shows the single most
/// profitable hedge; expanded it shows the top five.
class HedgeGroupCard extends StatefulWidget {
  const HedgeGroupCard({
    super.key,
    required this.group,
    required this.bets,
    required this.isSearching,
    this.inPlan = false,
    this.onPlaced,
  });

  final HedgeGroup group;

  /// The bets to show, best first (all of them, or only search matches).
  final List<Opportunity> bets;
  final bool isSearching;

  /// This combination's best bet is a step in the best plan.
  final bool inPlan;

  /// Called when the user taps "I placed this" on a bet (null hides it).
  final void Function(Opportunity bet)? onPlaced;

  @override
  State<HedgeGroupCard> createState() => _HedgeGroupCardState();
}

class _HedgeGroupCardState extends State<HedgeGroupCard> {
  static const _expandedCount = 5;
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final group = widget.group;
    final bets = widget.bets;
    final visible = _expanded
        ? bets.take(_expandedCount).toList()
        : bets.take(1).toList();
    final canExpand = bets.length > 1;

    final footer = widget.isSearching
        ? '${bets.length} matching ${bets.length == 1 ? 'hedge' : 'hedges'}'
        : '${group.betCount} ${group.betCount == 1 ? 'hedge' : 'hedges'} '
            'found for ${group.isTwoWay ? 'these boosts' : 'this boost'}';

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      clipBehavior: Clip.antiAlias,
      shape: widget.inPlan
          ? RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(12),
              side: BorderSide(color: Colors.indigo.shade300, width: 1.5),
            )
          : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Which boosts this card is about.
          Container(
            color: Colors.grey.shade100,
            padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final boost in group.boosts)
                        BoostLine(boost: boost),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    _Tag(
                      text: group.isTwoWay ? 'Two-way' : 'One-way',
                      background: Colors.white,
                      foreground: Colors.grey.shade800,
                    ),
                    if (widget.inPlan) ...[
                      const SizedBox(height: 4),
                      _Tag(
                        text: 'Best plan',
                        background: Colors.indigo.shade50,
                        foreground: Colors.indigo.shade800,
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
          // The best bet(s).
          for (var i = 0; i < visible.length; i++) ...[
            if (i > 0) const Divider(height: 1, indent: 16, endIndent: 16),
            OpportunityDetails(
              opportunity: visible[i],
              rank: _expanded ? i + 1 : null,
              onPlaced: widget.onPlaced == null
                  ? null
                  : () => widget.onPlaced!(visible[i]),
            ),
          ],
          // Expand / collapse and count.
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 8, 6),
            child: Row(
              children: [
                Expanded(
                  child: Text(footer, style: theme.textTheme.bodySmall),
                ),
                if (canExpand)
                  TextButton.icon(
                    onPressed: () => setState(() => _expanded = !_expanded),
                    icon: Icon(
                      _expanded ? Icons.expand_less : Icons.expand_more,
                    ),
                    label: Text(
                      _expanded
                          ? 'Show less'
                          : 'Show top ${math.min(_expandedCount, bets.length)}',
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// One boost on a hedge card: book color, nickname or "+25% NFL", details.
class BoostLine extends StatelessWidget {
  const BoostLine({super.key, required this.boost});

  final GroupBoost boost;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = boost.sportsbook.color;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 10,
            height: 10,
            margin: const EdgeInsets.only(top: 5, right: 8),
            decoration: BoxDecoration(
              color: color.shade600,
              shape: BoxShape.circle,
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Wrap(
                  spacing: 6,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Text(
                      boost.title,
                      style: theme.textTheme.titleSmall?.copyWith(
                        fontWeight: FontWeight.bold,
                        decoration:
                            boost.used ? TextDecoration.lineThrough : null,
                      ),
                    ),
                    if (boost.used)
                      _Tag(
                        text: 'Used',
                        background: Colors.grey.shade700,
                        foreground: Colors.white,
                      ),
                  ],
                ),
                Text(boost.details, style: theme.textTheme.bodySmall),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// One hedge: profit, the game, and exactly what to bet at each book.
class OpportunityDetails extends StatelessWidget {
  const OpportunityDetails({
    super.key,
    required this.opportunity,
    this.rank,
    this.onPlaced,
  });

  final Opportunity opportunity;
  final int? rank; // shown as "#2" when the card is expanded
  final VoidCallback? onPlaced; // shows "I placed this" when set

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final o = opportunity;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              if (rank != null) ...[
                Padding(
                  padding: const EdgeInsets.only(bottom: 3),
                  child: Text(
                    '#$rank',
                    style: theme.textTheme.titleSmall
                        ?.copyWith(color: Colors.grey.shade600),
                  ),
                ),
                const SizedBox(width: 8),
              ],
              Text(
                '+${formatCents(o.guaranteedProfit)}',
                style: theme.textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.bold,
                  color: Colors.green.shade800,
                ),
              ),
              const SizedBox(width: 8),
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text(
                  '${o.roiPercent.toStringAsFixed(1)}% return',
                  style: theme.textTheme.bodyMedium,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(o.game, style: theme.textTheme.titleMedium),
          Text(
            '${o.betType.label}, starts ${formatDateTime(o.commenceTime)}',
            style: theme.textTheme.bodySmall,
          ),
          const SizedBox(height: 6),
          for (final leg in o.legs) _LegRow(leg: leg),
          const SizedBox(height: 2),
          Row(
            children: [
              Expanded(
                child: Text(
                  'Total staked ${formatCents(o.totalStake)}',
                  style: theme.textTheme.bodySmall,
                ),
              ),
              if (onPlaced != null)
                OutlinedButton.icon(
                  onPressed: onPlaced,
                  icon: const Icon(Icons.check, size: 18),
                  label: const Text('I placed this'),
                  style: OutlinedButton.styleFrom(
                    visualDensity: VisualDensity.compact,
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// A compact summary of a hedge, used on boost cards and in pickers.
class PlacedBetSummary extends StatelessWidget {
  const PlacedBetSummary({super.key, required this.bet});

  final Opportunity bet;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '${bet.game}, ${formatDateTime(bet.commenceTime)}',
          style: theme.textTheme.bodyMedium
              ?.copyWith(fontWeight: FontWeight.w600),
        ),
        for (final leg in bet.legs)
          Text(
            '${leg.sportsbook.label}: ${leg.selection} '
            '(${formatOdds(leg.odds)}), ${formatCents(leg.stake)}'
            '${leg.isBoosted ? ', boosted' : ''}',
            style: theme.textTheme.bodySmall,
          ),
        Text(
          'Guaranteed +${formatCents(bet.guaranteedProfit)}',
          style: theme.textTheme.bodySmall?.copyWith(
            color: Colors.green.shade800,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}

class _LegRow extends StatelessWidget {
  const _LegRow({required this.leg});

  final OpportunityLeg leg;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final bookColor = leg.sportsbook.color;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 10,
            height: 10,
            margin: const EdgeInsets.only(top: 5, right: 10),
            decoration: BoxDecoration(
              color: bookColor.shade600,
              shape: BoxShape.circle,
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      leg.sportsbook.label,
                      style: theme.textTheme.labelLarge?.copyWith(
                        color: bookColor.shade800,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(width: 6),
                    _Tag(
                      text: leg.isBoosted
                          ? '+${formatPercent(leg.boostPercent)} boost'
                          : 'No boost',
                      background: leg.isBoosted
                          ? Colors.amber.shade100
                          : Colors.grey.shade200,
                      foreground: leg.isBoosted
                          ? Colors.brown.shade800
                          : Colors.grey.shade700,
                    ),
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  '${leg.selection} (${formatOdds(leg.odds)})',
                  style: theme.textTheme.bodyLarge,
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                'Bet ${formatCents(leg.stake)}',
                style: theme.textTheme.titleSmall?.copyWith(
                  fontWeight: FontWeight.bold,
                ),
              ),
              Text(
                'Pays ${formatCents(leg.payout)}',
                style: theme.textTheme.bodySmall,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag({
    required this.text,
    required this.background,
    required this.foreground,
  });

  final String text;
  final Color background;
  final Color foreground;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: foreground,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
