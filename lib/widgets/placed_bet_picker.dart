import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/opportunity.dart';
import '../models/profit_boost.dart';
import 'opportunity_card.dart';

/// The user's answer: the hedge they placed and with which amounts, or a
/// null bet for "just mark used".
class PlacedChoice {
  const PlacedChoice(this.bet, [this.version]);
  final Opportunity? bet;
  final HedgeVersion? version;
}

/// Asks which amounts were bet, when a hedge has more than one version.
Future<HedgeVersion?> _pickVersion(BuildContext context, Opportunity bet) {
  if (bet.versions.length == 1) return Future.value(bet.safest);
  return showDialog<HedgeVersion>(
    context: context,
    builder: (ctx) => SimpleDialog(
      title: const Text('Which amounts did you bet?'),
      children: [
        for (final v in bet.versions)
          SimpleDialogOption(
            onPressed: () => Navigator.of(ctx).pop(v),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: PlacedBetSummary(bet: bet, version: v),
            ),
          ),
      ],
    ),
  );
}

/// Asks which hedge was placed with [boost]. Returns null if dismissed.
Future<PlacedChoice?> showPlacedBetPicker({
  required BuildContext context,
  required ProfitBoost boost,
  required List<HedgeGroup> groups,
}) {
  // Every current hedge that uses this boost (and no other used boost).
  final candidates = <(HedgeGroup, Opportunity)>[
    for (final group in groups)
      if (group.containsBoost(boost.id) &&
          group.usedBoostIds.every((id) => id == boost.id))
        for (final bet in group.bets) (group, bet),
  ]..sort((a, b) => b.$2.guaranteedProfit.compareTo(a.$2.guaranteedProfit));
  final shown = math.min(candidates.length, 30);

  return showModalBottomSheet<PlacedChoice>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: true,
    builder: (sheetContext) {
      final theme = Theme.of(sheetContext);
      final boostName = boost.hasNickname
          ? '"${boost.nickname!.trim()}" (${boost.sportsbook.label} ${boost.shortLabel})'
          : '${boost.sportsbook.label} ${boost.shortLabel}';

      return SizedBox(
        height: MediaQuery.of(sheetContext).size.height * 0.8,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Which hedge did you place?',
                      style: theme.textTheme.titleLarge),
                  const SizedBox(height: 2),
                  Text(boostName, style: theme.textTheme.bodyMedium),
                ],
              ),
            ),
            ListTile(
              leading: const Icon(Icons.check_circle_outline),
              title: const Text('Just mark it used'),
              subtitle: const Text('Don\'t record a hedge'),
              onTap: () =>
                  Navigator.of(sheetContext).pop(const PlacedChoice(null)),
            ),
            const Divider(height: 1),
            Expanded(
              child: shown == 0
                  ? const Center(
                      child: Padding(
                        padding: EdgeInsets.all(24),
                        child: Text(
                          'No current hedges use this boost. You can still '
                          'mark it used above.',
                          textAlign: TextAlign.center,
                        ),
                      ),
                    )
                  : ListView.separated(
                      itemCount: shown,
                      separatorBuilder: (_, _) => const Divider(height: 1),
                      itemBuilder: (_, i) {
                        final (group, bet) = candidates[i];
                        final others =
                            group.boosts.where((b) => b.id != boost.id);
                        return ListTile(
                          title: PlacedBetSummary(bet: bet),
                          subtitle: others.isEmpty
                              ? null
                              : Padding(
                                  padding: const EdgeInsets.only(top: 4),
                                  child: Text(
                                    'Also marks '
                                    '${others.map((b) => '${b.sportsbook.label} ${b.title}').join(', ')} '
                                    'used (two-way hedge)',
                                    style: TextStyle(
                                        color: Colors.indigo.shade800),
                                  ),
                                ),
                          onTap: () async {
                            final version = await _pickVersion(sheetContext, bet);
                            if (version != null && sheetContext.mounted) {
                              Navigator.of(sheetContext).pop(PlacedChoice(bet, version));
                            }
                          },
                        );
                      },
                    ),
            ),
          ],
        ),
      );
    },
  );
}