import 'package:flutter/material.dart';

import '../models/profit_boost.dart';
import '../utils/format.dart';
import 'opportunity_card.dart';

class BoostCard extends StatelessWidget {
  const BoostCard({
    super.key,
    required this.boost,
    required this.accent,
    required this.onEdit,
    required this.onDelete,
    required this.onToggleUsed,
  });

  final ProfitBoost boost;
  final Color accent;
  final VoidCallback onEdit;
  final VoidCallback onDelete;
  final VoidCallback onToggleUsed;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final status = boost.statusAt(DateTime.now());
    final used = boost.used;
    final color = used ? Colors.grey.shade600 : accent;

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      color: used ? Colors.grey.shade50 : null,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(
          color: used ? Colors.grey.shade400 : accent,
          width: 1.5,
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 6, 4, 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Row(
                    children: [
                      Text(
                        '+${formatPercent(boost.percentBoost)}',
                        style: theme.textTheme.headlineSmall?.copyWith(
                          fontWeight: FontWeight.bold,
                          color: color,
                        ),
                      ),
                      const SizedBox(width: 8),
                      Flexible(
                        child: Text(
                          boost.betType.label,
                          style: theme.textTheme.titleMedium,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                ),
                used ? const _UsedBadge() : _StatusBadge(status: status),
                IconButton(
                  icon: const Icon(Icons.edit_outlined),
                  tooltip: 'Edit boost',
                  visualDensity: VisualDensity.compact,
                  onPressed: onEdit,
                ),
                IconButton(
                  icon: const Icon(Icons.delete_outline),
                  tooltip: 'Delete boost',
                  visualDensity: VisualDensity.compact,
                  onPressed: onDelete,
                ),
              ],
            ),
            if (boost.hasNickname)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(
                  boost.nickname!.trim(),
                  style: theme.textTheme.titleSmall
                      ?.copyWith(fontWeight: FontWeight.w600),
                ),
              ),
            Wrap(
              spacing: 16,
              runSpacing: 6,
              children: [
                _Detail(
                  icon: Icons.trending_up,
                  text:
                      'Odds ${formatOdds(boost.minOdds)} to ${formatOdds(boost.maxOdds)}',
                ),
                _Detail(
                  icon: Icons.attach_money,
                  text: 'Max bet ${formatMoney(boost.maxBet)}',
                ),
              ],
            ),
            const SizedBox(height: 6),
            _Detail(
              icon: Icons.schedule,
              text:
                  '${formatDateTime(boost.validFrom)} – ${formatDateTime(boost.validUntil)}',
            ),
            if (boost.isSingleGame) ...[
              const SizedBox(height: 6),
              _Detail(
                icon: Icons.sports_score,
                text: 'Only ${boost.eventName ?? 'one game'}'
                    '${boost.eventStart == null ? '' : ', ${formatDateTime(boost.eventStart!)}'}',
              ),
            ],
            if (boost.hasPropFilter) ...[
              const SizedBox(height: 6),
              _Detail(
                icon: Icons.tune,
                text: 'Only ${PropType.describe(boost.propTypes)} bets',
              ),
            ],
            if (!boost.betType.autoMatched) ...[
              const SizedBox(height: 6),
              const _Detail(
                icon: Icons.info_outline,
                text: 'Not checked for hedges (league not supported)',
              ),
            ],
            if (used) ...[
              const SizedBox(height: 10),
              Container(
                width: double.infinity,
                margin: const EdgeInsets.only(right: 12),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Colors.grey.shade200,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      boost.usedAt == null
                          ? 'Used'
                          : 'Used ${formatDateTime(boost.usedAt!)}',
                      style: theme.textTheme.labelLarge,
                    ),
                    const SizedBox(height: 4),
                    boost.placedBet == null
                        ? Text('No hedge recorded',
                            style: theme.textTheme.bodySmall)
                        : PlacedBetSummary(bet: boost.placedBet!),
                  ],
                ),
              ),
            ],
            Align(
              alignment: Alignment.centerRight,
              child: Padding(
                padding: const EdgeInsets.only(top: 4, right: 4),
                child: used
                    ? TextButton.icon(
                        onPressed: onToggleUsed,
                        icon: const Icon(Icons.undo, size: 18),
                        label: const Text('Mark unused'),
                      )
                    : FilledButton.tonalIcon(
                        onPressed: onToggleUsed,
                        icon: const Icon(Icons.check_circle_outline, size: 18),
                        label: const Text('Mark used'),
                      ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Detail extends StatelessWidget {
  const _Detail({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 16, color: Colors.grey.shade700),
        const SizedBox(width: 4),
        Flexible(child: Text(text)),
      ],
    );
  }
}

class _UsedBadge extends StatelessWidget {
  const _UsedBadge();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: Colors.grey.shade700,
        borderRadius: BorderRadius.circular(20),
      ),
      child: const Text(
        'Used',
        style: TextStyle(
          color: Colors.white,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

class _StatusBadge extends StatelessWidget {
  const _StatusBadge({required this.status});

  final BoostStatus status;

  @override
  Widget build(BuildContext context) {
    final (label, bg, fg) = switch (status) {
      BoostStatus.active => ('Active', Colors.green.shade100, Colors.green.shade900),
      BoostStatus.upcoming => ('Upcoming', Colors.amber.shade100, Colors.brown.shade800),
      BoostStatus.expired => ('Expired', Colors.grey.shade300, Colors.grey.shade800),
    };

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        label,
        style: TextStyle(color: fg, fontSize: 12, fontWeight: FontWeight.w600),
      ),
    );
  }
}