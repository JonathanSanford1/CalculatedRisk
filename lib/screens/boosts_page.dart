import 'package:flutter/material.dart';

import '../models/opportunity.dart';
import '../models/profit_boost.dart';
import '../services/boost_repository.dart';
import '../widgets/boost_card.dart';
import '../widgets/boost_form_sheet.dart';
import '../widgets/error_view.dart';
import '../widgets/placed_bet_picker.dart';
import '../widgets/sportsbook_style.dart';

/// The two-section screen: DraftKings boosts (green) and FanDuel boosts (blue).
class BoostsPage extends StatefulWidget {
  const BoostsPage({super.key, required this.repository});

  final BoostRepository repository;

  @override
  State<BoostsPage> createState() => _BoostsPageState();
}

class _BoostsPageState extends State<BoostsPage> {
  late Stream<List<ProfitBoost>> _boostStream;

  @override
  void initState() {
    super.initState();
    _boostStream = widget.repository.watchBoosts();
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  /// Opens the form to create a boost (existing == null) or edit one.
  Future<void> _openForm({
    required Sportsbook sportsbook,
    ProfitBoost? existing,
  }) async {
    final boost = await showModalBottomSheet<ProfitBoost>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      builder: (_) => BoostFormSheet(sportsbook: sportsbook, initial: existing),
    );
    if (boost == null) return;

    // Not awaited: the card updates immediately and syncs in the background.
    widget.repository
        .saveBoost(boost, isNew: existing == null)
        .catchError((Object e) {
      _showError('Couldn\'t save the boost: $e');
    });
  }

  String _describe(ProfitBoost b) => b.hasNickname
      ? '"${b.nickname!.trim()}"'
      : 'the ${b.sportsbook.label} ${b.shortLabel} boost';

  /// Mark used (asking which hedge was placed) or mark unused again.
  Future<void> _toggleUsed(ProfitBoost boost, List<ProfitBoost> all) async {
    if (boost.used) {
      // A two-way hedge marks two boosts used; restore them together.
      final placedId = boost.placedBet?.id;
      final linked = placedId == null || placedId.isEmpty
          ? <ProfitBoost>[]
          : all
              .where((b) =>
                  b.id != boost.id && b.used && b.placedBet?.id == placedId)
              .toList();

      final confirmed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Mark as unused?'),
          content: Text(
            linked.isEmpty
                ? 'This boost will be available again and any recorded '
                    'hedge will be cleared.'
                : 'This boost and ${linked.map(_describe).join(', ')} were '
                    'placed together on one hedge, so both will be '
                    'available again.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Mark unused'),
            ),
          ],
        ),
      );
      if (confirmed != true) return;
      widget.repository
          .markUnused([boost.id, ...linked.map((b) => b.id)])
          .catchError((Object e) {
        _showError('Couldn\'t update the boost: $e');
      });
      return;
    }

    var groups = <HedgeGroup>[];
    try {
      groups = await widget.repository.fetchHedgeGroups();
    } catch (_) {
      // Offline or not loaded yet: the picker still offers "just mark used".
    }
    if (!mounted) return;

    final choice = await showPlacedBetPicker(
      context: context,
      boost: boost,
      groups: groups,
    );
    if (choice == null) return;

    final bet = choice.bet;
    final update = bet == null
        ? widget.repository.markUsed(boost.id)
        : widget.repository.markPlaced(bet);
    update.catchError((Object e) {
      _showError('Couldn\'t mark the boost used: $e');
    });
  }

  Future<void> _deleteBoost(ProfitBoost boost) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete this boost?'),
        content: Text(
          boost.hasNickname
              ? '"${boost.nickname!.trim()}" (${boost.sportsbook.label} '
                  '${boost.shortLabel}) will be removed.'
              : 'The ${boost.sportsbook.label} ${boost.shortLabel} boost '
                  'will be removed.',
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
    if (confirmed != true) return;

    widget.repository.deleteBoost(boost.id).catchError((Object e) {
      _showError('Couldn\'t delete the boost: $e');
    });
  }

  List<ProfitBoost> _forBook(List<ProfitBoost> all, Sportsbook book) =>
      all.where((b) => b.sportsbook == book).toList()
        ..sort((a, b) => a.validUntil.compareTo(b.validUntil));

  @override
  Widget build(BuildContext context) {
    return StreamBuilder<List<ProfitBoost>>(
      stream: _boostStream,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          return ErrorView(
            message: 'Couldn\'t load your boosts.',
            details: '${snapshot.error}',
            onRetry: () => setState(
              () => _boostStream = widget.repository.watchBoosts(),
            ),
          );
        }
        if (!snapshot.hasData) {
          return const Center(child: CircularProgressIndicator());
        }

        final boosts = snapshot.data!;
        return Column(
          children: [
            for (final book in Sportsbook.values)
              Expanded(
                child: _BoostSection(
                  sportsbook: book,
                  boosts: _forBook(boosts, book),
                  onAdd: () => _openForm(sportsbook: book),
                  onEdit: (boost) =>
                      _openForm(sportsbook: boost.sportsbook, existing: boost),
                  onDelete: _deleteBoost,
                  onToggleUsed: (boost) => _toggleUsed(boost, boosts),
                ),
              ),
          ],
        );
      },
    );
  }
}

class _BoostSection extends StatelessWidget {
  const _BoostSection({
    required this.sportsbook,
    required this.boosts,
    required this.onAdd,
    required this.onEdit,
    required this.onDelete,
    required this.onToggleUsed,
  });

  final Sportsbook sportsbook;
  final List<ProfitBoost> boosts;
  final VoidCallback onAdd;
  final void Function(ProfitBoost) onEdit;
  final void Function(ProfitBoost) onDelete;
  final void Function(ProfitBoost) onToggleUsed;

  @override
  Widget build(BuildContext context) {
    final color = sportsbook.color;
    final headerStyle = Theme.of(context)
        .textTheme
        .titleMedium
        ?.copyWith(color: Colors.white, fontWeight: FontWeight.bold);
    final unused = boosts.where((b) => !b.used).toList();
    final used = boosts.where((b) => b.used).toList();

    Widget card(ProfitBoost boost) => BoostCard(
          key: ValueKey(boost.id),
          boost: boost,
          accent: color.shade700,
          onEdit: () => onEdit(boost),
          onDelete: () => onDelete(boost),
          onToggleUsed: () => onToggleUsed(boost),
        );

    return Container(
      color: color.shade50,
      child: Column(
        children: [
          Container(
            color: color.shade600,
            padding: const EdgeInsets.fromLTRB(16, 2, 4, 2),
            child: Row(
              children: [
                Text(
                  '${sportsbook.label} (${unused.length})',
                  style: headerStyle,
                ),
                const Spacer(),
                IconButton(
                  icon: const Icon(Icons.add_circle_outline,
                      color: Colors.white),
                  tooltip: 'Add ${sportsbook.label} boost',
                  onPressed: onAdd,
                ),
              ],
            ),
          ),
          Expanded(
            child: boosts.isEmpty
                ? Center(
                    child: Text(
                      'No boosts yet. Tap + to add one.',
                      style: TextStyle(color: color.shade800),
                    ),
                  )
                : ListView(
                    padding: const EdgeInsets.symmetric(vertical: 6),
                    children: [
                      for (final boost in unused) card(boost),
                      if (unused.isEmpty)
                        Padding(
                          padding: const EdgeInsets.all(16),
                          child: Text(
                            'Every boost here is used. Tap + to add one.',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: color.shade800),
                          ),
                        ),
                      if (used.isNotEmpty)
                        Theme(
                          // Hide ExpansionTile's top/bottom divider lines.
                          data: Theme.of(context)
                              .copyWith(dividerColor: Colors.transparent),
                          child: ExpansionTile(
                            key: PageStorageKey('used-${sportsbook.name}'),
                            leading: Icon(Icons.inventory_2_outlined,
                                color: Colors.grey.shade700),
                            title: Text('Used (${used.length})'),
                            subtitle: const Text('Tap to show'),
                            children: [for (final boost in used) card(boost)],
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
