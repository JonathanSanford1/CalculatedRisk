import 'package:flutter/material.dart';

import '../models/profit_boost.dart';
import '../utils/format.dart';
import '../widgets/boost_card.dart';
import '../widgets/boost_form_sheet.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  // All boosts live here for now (in memory; they reset when the app restarts).
  final List<ProfitBoost> _boosts = [];

  /// Boosts for one section, soonest-expiring first.
  List<ProfitBoost> _boostsFor(BoostSection section) => _boosts
      .where((b) => b.section == section)
      .toList()
    ..sort((a, b) => a.validUntil.compareTo(b.validUntil));

  Future<void> _addBoost(BoostSection section) async {
    final boost = await showModalBottomSheet<ProfitBoost>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      builder: (_) => BoostFormSheet(section: section),
    );
    if (boost != null) setState(() => _boosts.add(boost));
  }

  Future<void> _deleteBoost(ProfitBoost boost) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete this boost?'),
        content: Text(
          'The +${formatPercent(boost.percentBoost)} ${boost.betType.label} boost will be removed.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      setState(() => _boosts.removeWhere((b) => b.id == boost.id));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('CalculatedRisk')),
      body: Column(
        children: [
          Expanded(
            child: _BoostSectionView(
              title: 'Green',
              color: Colors.green,
              boosts: _boostsFor(BoostSection.green),
              onAdd: () => _addBoost(BoostSection.green),
              onDelete: _deleteBoost,
            ),
          ),
          Expanded(
            child: _BoostSectionView(
              title: 'Blue',
              color: Colors.blue,
              boosts: _boostsFor(BoostSection.blue),
              onAdd: () => _addBoost(BoostSection.blue),
              onDelete: _deleteBoost,
            ),
          ),
        ],
      ),
    );
  }
}

class _BoostSectionView extends StatelessWidget {
  const _BoostSectionView({
    required this.title,
    required this.color,
    required this.boosts,
    required this.onAdd,
    required this.onDelete,
  });

  final String title;
  final MaterialColor color;
  final List<ProfitBoost> boosts;
  final VoidCallback onAdd;
  final void Function(ProfitBoost) onDelete;

  @override
  Widget build(BuildContext context) {
    final headerStyle = Theme.of(context)
        .textTheme
        .titleMedium
        ?.copyWith(color: Colors.white, fontWeight: FontWeight.bold);

    return Container(
      color: color.shade50,
      child: Column(
        children: [
          // Section header with the add button.
          Container(
            color: color.shade600,
            padding: const EdgeInsets.fromLTRB(16, 2, 4, 2),
            child: Row(
              children: [
                Text('$title (${boosts.length})', style: headerStyle),
                const Spacer(),
                IconButton(
                  icon: const Icon(Icons.add_circle_outline, color: Colors.white),
                  tooltip: 'Add $title boost',
                  onPressed: onAdd,
                ),
              ],
            ),
          ),
          // Card list.
          Expanded(
            child: boosts.isEmpty
                ? Center(
                    child: Text(
                      'No boosts yet. Tap + to add one.',
                      style: TextStyle(color: color.shade800),
                    ),
                  )
                : ListView.builder(
                    padding: const EdgeInsets.symmetric(vertical: 6),
                    itemCount: boosts.length,
                    itemBuilder: (_, i) => BoostCard(
                      key: ValueKey(boosts[i].id),
                      boost: boosts[i],
                      accent: color.shade700,
                      onDelete: () => onDelete(boosts[i]),
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}