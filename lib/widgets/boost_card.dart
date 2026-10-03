import 'package:flutter/material.dart';

import '../models/profit_boost.dart';
import '../utils/format.dart';

class BoostCard extends StatelessWidget {
  const BoostCard({
    super.key,
    required this.boost,
    required this.accent,
    required this.onDelete,
  });

  final ProfitBoost boost;
  final Color accent;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final status = boost.statusAt(DateTime.now());

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: accent, width: 1.5),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 4, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(
                  '+${formatPercent(boost.percentBoost)}',
                  style: theme.textTheme.headlineSmall?.copyWith(
                    fontWeight: FontWeight.bold,
                    color: accent,
                  ),
                ),
                const SizedBox(width: 8),
                Text(boost.betType.label, style: theme.textTheme.titleMedium),
                const Spacer(),
                _StatusBadge(status: status),
                IconButton(
                  icon: const Icon(Icons.delete_outline),
                  tooltip: 'Delete boost',
                  onPressed: onDelete,
                ),
              ],
            ),
            const SizedBox(height: 4),
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