import 'package:flutter/material.dart';

import '../models/profit_boost.dart';

/// Opens a searchable list of leagues, grouped by sport.
/// Returns the chosen league, or null if dismissed.
Future<BetType?> showLeaguePicker(BuildContext context, BetType selected) {
  return showModalBottomSheet<BetType>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: true,
    builder: (_) => _LeaguePicker(selected: selected),
  );
}

class _LeaguePicker extends StatefulWidget {
  const _LeaguePicker({required this.selected});

  final BetType selected;

  @override
  State<_LeaguePicker> createState() => _LeaguePickerState();
}

class _LeaguePickerState extends State<_LeaguePicker> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final query = _query.trim().toLowerCase();
    final matches = BetType.values
        .where((type) =>
            query.isEmpty ||
            type.label.toLowerCase().contains(query) ||
            type.category.label.toLowerCase().contains(query))
        .toList();

    final rows = <Widget>[];
    for (final category in SportCategory.values) {
      final leagues = matches.where((t) => t.category == category).toList();
      if (leagues.isEmpty) continue;
      rows.add(Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
        child: Text(
          category.label,
          style: theme.textTheme.titleSmall?.copyWith(
            color: theme.colorScheme.primary,
            fontWeight: FontWeight.bold,
          ),
        ),
      ));
      for (final league in leagues) {
        rows.add(ListTile(
          title: Text(league.label),
          subtitle: league.autoMatched
              ? null
              : const Text('Saved, but not checked for hedges'),
          trailing: league == widget.selected ? const Icon(Icons.check) : null,
          onTap: () => Navigator.of(context).pop(league),
        ));
      }
    }

    return SizedBox(
      height: MediaQuery.of(context).size.height * 0.8,
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: TextField(
              decoration: const InputDecoration(
                hintText: 'Search leagues',
                prefixIcon: Icon(Icons.search),
                border: OutlineInputBorder(),
                isDense: true,
              ),
              onChanged: (value) => setState(() => _query = value),
            ),
          ),
          Expanded(
            child: rows.isEmpty
                ? const Center(
                    child: Text('No leagues match. Try "Other".'),
                  )
                : ListView(
                    padding: EdgeInsets.only(
                      bottom: MediaQuery.of(context).viewInsets.bottom + 16,
                    ),
                    children: rows,
                  ),
          ),
        ],
      ),
    );
  }
}
