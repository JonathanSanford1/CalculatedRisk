import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/hedge_goal.dart';
import '../models/opportunity.dart';
import '../utils/format.dart';
import 'sportsbook_style.dart';

/// One card per boost combination. Collapsed it shows the best hedge for the
/// chosen goal; expanded it shows the top five.
class HedgeGroupCard extends StatefulWidget {
  const HedgeGroupCard({
    super.key,
    required this.group,
    required this.bets,
    required this.isSearching,
    required this.goal,
    this.inPlan = false,
    this.onPlaced,
  });

  final HedgeGroup group;

  /// The bets to show, best first for [goal] (all, or only search matches).
  final List<Opportunity> bets;
  final bool isSearching;
  final HedgeGoal goal;

  /// This combination's best bet is a step in the best plan.
  final bool inPlan;

  /// Called when the user taps "I placed this" (null hides the button).
  final void Function(Opportunity bet, HedgeVersion version)? onPlaced;

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
    final visible = bets.take(_expanded ? _expandedCount : 1).toList();
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
                      for (final boost in group.boosts) BoostLine(boost: boost),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Tag(
                      text: group.isTwoWay ? 'Two-way' : 'One-way',
                      background: Colors.white,
                      foreground: Colors.grey.shade800,
                    ),
                    if (widget.inPlan) ...[
                      const SizedBox(height: 4),
                      Tag(
                        text: 'Best plan',
                        background: Colors.indigo.shade50,
                        foreground: Colors.indigo.shade800,
                      ),
                    ],
                    if (group.upcoming && group.availableFrom != null) ...[
                      const SizedBox(height: 4),
                      Tag(
                        text: 'Opens ${formatDateTime(group.availableFrom!)}',
                        background: Colors.amber.shade100,
                        foreground: Colors.brown.shade800,
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
          for (var i = 0; i < visible.length; i++) ...[
            if (i > 0) const Divider(height: 1, thickness: 1),
            OpportunityDetails(
              key: ValueKey('${visible[i].id}-${widget.goal.name}'),
              opportunity: visible[i],
              goal: widget.goal,
              rank: _expanded ? i + 1 : null,
              onPlaced: widget.onPlaced == null || group.upcoming
                  ? null
                  : (version) => widget.onPlaced!(visible[i], version),
            ),
          ],
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 8, 6),
            child: Row(
              children: [
                Expanded(child: Text(footer, style: theme.textTheme.bodySmall)),
                if (canExpand)
                  TextButton.icon(
                    onPressed: () => setState(() => _expanded = !_expanded),
                    icon: Icon(_expanded ? Icons.expand_less : Icons.expand_more),
                    label: Text(_expanded
                        ? 'Show less'
                        : 'Show top ${math.min(_expandedCount, bets.length)}'),
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
            decoration: BoxDecoration(color: color.shade600, shape: BoxShape.circle),
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
                        decoration: boost.used ? TextDecoration.lineThrough : null,
                      ),
                    ),
                    if (boost.used)
                      Tag(
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

/// One hedge: what you're guaranteed and could win, the game, and exactly
/// what to bet at each book, for the selected stake version.
class OpportunityDetails extends StatefulWidget {
  const OpportunityDetails({
    super.key,
    required this.opportunity,
    required this.goal,
    this.rank,
    this.initialVersionKey,
    this.onPlaced,
  });

  final Opportunity opportunity;
  final HedgeGoal goal;
  final int? rank; // shown as "#2" when the card is expanded
  final String? initialVersionKey; // otherwise chosen by the goal
  final void Function(HedgeVersion version)? onPlaced;

  @override
  State<OpportunityDetails> createState() => _OpportunityDetailsState();
}

class _OpportunityDetailsState extends State<OpportunityDetails> {
  late String _versionKey;

  @override
  void initState() {
    super.initState();
    _versionKey = widget.initialVersionKey ??
        widget.opportunity.versionFor(widget.goal).key;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final o = widget.opportunity;
    final version = o.versionByKey(_versionKey);
    final legs = o.legsFor(version);
    final upside = version.maxProfit - version.guaranteedProfit > 0.005;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              if (widget.rank != null) ...[
                Padding(
                  padding: const EdgeInsets.only(bottom: 3),
                  child: Text('#${widget.rank}',
                      style: theme.textTheme.titleSmall
                          ?.copyWith(color: Colors.grey.shade600)),
                ),
                const SizedBox(width: 8),
              ],
              Text(
                '+${formatCents(version.guaranteedProfit)}',
                style: theme.textTheme.headlineSmall?.copyWith(
                  fontWeight: FontWeight.bold,
                  color: Colors.green.shade800,
                ),
              ),
              const SizedBox(width: 6),
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text('guaranteed', style: theme.textTheme.bodyMedium),
              ),
              const Spacer(),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text('up to', style: theme.textTheme.bodySmall),
                  Text(
                    '+${formatCents(version.maxProfit)}',
                    style: theme.textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.bold,
                      color: upside ? Colors.indigo.shade700 : Colors.grey.shade700,
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(o.game, style: theme.textTheme.titleMedium),
          Text(
            '${o.betType.label}'
            '${o.marketLabel.isEmpty ? '' : ' ${o.marketLabel}'}'
            ', starts ${formatDateTime(o.commenceTime)}',
            style: theme.textTheme.bodySmall,
          ),
          if (o.versions.length > 1) ...[
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: [
                for (final v in o.versions)
                  ChoiceChip(
                    label: Text(v.label),
                    selected: v.key == version.key,
                    visualDensity: VisualDensity.compact,
                    onSelected: (_) => setState(() => _versionKey = v.key),
                  ),
              ],
            ),
          ],
          const SizedBox(height: 6),
          for (final leg in legs) _LegRow(leg: leg),
          if (o.middle != null) ...[
            const SizedBox(height: 4),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.stars_outlined, size: 16, color: Colors.indigo.shade700),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(o.middle!,
                      style: theme.textTheme.bodySmall
                          ?.copyWith(color: Colors.indigo.shade900)),
                ),
              ],
            ),
          ],
          const SizedBox(height: 2),
          Row(
            children: [
              Expanded(
                child: Text('Total staked ${formatCents(version.totalStake)}',
                    style: theme.textTheme.bodySmall),
              ),
              if (widget.onPlaced != null)
                OutlinedButton.icon(
                  onPressed: () => widget.onPlaced!(version),
                  icon: const Icon(Icons.check, size: 18),
                  label: const Text('I placed this'),
                  style: OutlinedButton.styleFrom(
                      visualDensity: VisualDensity.compact),
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
  const PlacedBetSummary({super.key, required this.bet, this.version});

  final Opportunity bet;
  final HedgeVersion? version; // defaults to the safest

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final v = version ?? bet.safest;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('${bet.game}, ${formatDateTime(bet.commenceTime)}',
            style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600)),
        for (final leg in bet.legsFor(v))
          Text(
            '${leg.sportsbook.label}: ${leg.selection} '
            '(${formatOdds(leg.odds)}), ${formatCents(leg.stake)}'
            '${leg.isBoosted ? ', boosted' : ''}',
            style: theme.textTheme.bodySmall,
          ),
        Text(
          'Guaranteed +${formatCents(v.guaranteedProfit)}, up to +${formatCents(v.maxProfit)}',
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
            decoration: BoxDecoration(color: bookColor.shade600, shape: BoxShape.circle),
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
                    Tag(
                      text: leg.isBoosted
                          ? '+${formatPercent(leg.boostPercent)} boost'
                          : 'No boost',
                      background: leg.isBoosted ? Colors.amber.shade100 : Colors.grey.shade200,
                      foreground: leg.isBoosted ? Colors.brown.shade800 : Colors.grey.shade700,
                    ),
                  ],
                ),
                const SizedBox(height: 2),
                Text('${leg.selection} (${formatOdds(leg.odds)})',
                    style: theme.textTheme.bodyLarge),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text('Bet ${formatCents(leg.stake)}',
                  style: theme.textTheme.titleSmall?.copyWith(fontWeight: FontWeight.bold)),
              Text('Pays ${formatCents(leg.payout)}', style: theme.textTheme.bodySmall),
            ],
          ),
        ],
      ),
    );
  }
}

/// A small rounded label.
class Tag extends StatelessWidget {
  const Tag({
    super.key,
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
      decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(20)),
      child: Text(text,
          style: TextStyle(color: foreground, fontSize: 12, fontWeight: FontWeight.w600)),
    );
  }
}
