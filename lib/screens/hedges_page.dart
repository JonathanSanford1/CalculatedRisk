import 'package:cloud_functions/cloud_functions.dart';
import 'package:flutter/material.dart';

import '../models/best_plan.dart';
import '../models/opportunity.dart';
import '../services/boost_repository.dart';
import '../utils/format.dart';
import '../widgets/error_view.dart';
import '../widgets/opportunity_card.dart';
import '../widgets/plan_card.dart';

/// Hedges found by the Cloud Function: the best plan first, then one card per
/// boost combination (best first), with a search bar. Cards that use a boost
/// marked used are collected in a collapsed section at the bottom.
class HedgesPage extends StatefulWidget {
  const HedgesPage({super.key, required this.repository});

  final BoostRepository repository;

  @override
  State<HedgesPage> createState() => _HedgesPageState();
}

class _HedgesPageState extends State<HedgesPage> {
  final _searchCtrl = TextEditingController();
  late Stream<List<HedgeGroup>> _groupStream;
  late Stream<RefreshStatus?> _statusStream;
  late Stream<BestPlan?> _planStream;
  bool _refreshing = false;
  String _query = '';

  @override
  void initState() {
    super.initState();
    _groupStream = widget.repository.watchHedgeGroups();
    _statusStream = widget.repository.watchStatus();
    _planStream = widget.repository.watchPlan();
  }

  @override
  void dispose() {
    _searchCtrl.dispose();
    super.dispose();
  }

  void _showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(message)));
  }

  /// Runs the Cloud Function now. New results arrive through the stream.
  Future<void> _refresh() async {
    if (_refreshing) return;
    setState(() => _refreshing = true);
    try {
      _showMessage(await widget.repository.refreshNow());
    } on FirebaseFunctionsException catch (e) {
      _showMessage(e.message ?? 'Refresh failed (${e.code}).');
    } catch (e) {
      _showMessage('Refresh failed: $e');
    } finally {
      if (mounted) setState(() => _refreshing = false);
    }
  }

  /// "I placed this": marks the bet's boost(s) used and records the hedge.
  Future<void> _placeBet(Opportunity bet, List<GroupBoost> boosts) async {
    final used = boosts.where((b) => bet.boostIds.contains(b.id)).toList();
    final names = used.map((b) => '${b.sportsbook.label} ${b.title}').join(' and ');
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Mark this hedge as placed?'),
        content: Text(
          '$names will be marked used, and this hedge will be saved on '
          '${used.length == 1 ? 'it' : 'them'}. You can undo this from the '
          'Boosts tab.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Mark placed'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await widget.repository.markPlaced(bet);
      _showMessage('Marked as placed. The plan will update in a moment.');
    } catch (e) {
      _showMessage('Couldn\'t mark the hedge placed: $e');
    }
  }

  /// Bets in [group] matching the search. Every word must match somewhere.
  /// Searching a boost's name, book, or league shows all of its bets.
  List<Opportunity> _matchingBets(HedgeGroup group, List<String> words) {
    if (words.isEmpty) return group.bets;
    bool matches(String text) => words.every(text.contains);
    if (matches(group.boostSearchText)) return group.bets;
    return group.bets
        .where((bet) => matches('${group.boostSearchText} ${bet.searchText}'))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        StreamBuilder<RefreshStatus?>(
          stream: _statusStream,
          builder: (context, snapshot) => _StatusBar(
            status: snapshot.data,
            refreshing: _refreshing,
            onRefresh: _refresh,
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 4),
          child: TextField(
            controller: _searchCtrl,
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              hintText: 'Search teams, leagues, books, boosts',
              prefixIcon: const Icon(Icons.search),
              suffixIcon: _query.isEmpty
                  ? null
                  : IconButton(
                      icon: const Icon(Icons.clear),
                      tooltip: 'Clear search',
                      onPressed: () {
                        _searchCtrl.clear();
                        setState(() => _query = '');
                      },
                    ),
              isDense: true,
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(24),
              ),
            ),
            onChanged: (value) => setState(() => _query = value),
          ),
        ),
        Expanded(
          child: StreamBuilder<BestPlan?>(
            stream: _planStream,
            builder: (context, planSnapshot) =>
                StreamBuilder<List<HedgeGroup>>(
              stream: _groupStream,
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  return ErrorView(
                    message: 'Couldn\'t load hedges.',
                    details: '${snapshot.error}',
                    onRetry: () => setState(
                      () => _groupStream = widget.repository.watchHedgeGroups(),
                    ),
                  );
                }
                if (!snapshot.hasData) {
                  return const Center(child: CircularProgressIndicator());
                }
                return _buildList(snapshot.data!, planSnapshot.data);
              },
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildList(List<HedgeGroup> groups, BestPlan? plan) {
    final words = _query
        .toLowerCase()
        .split(RegExp(r'\s+'))
        .where((w) => w.isNotEmpty)
        .toList();
    final isSearching = words.isNotEmpty;

    // Pair each group with the bets to show, dropping groups with no
    // matches, and order by the best visible bet.
    final results = <(HedgeGroup, List<Opportunity>)>[
      for (final group in groups) (group, _matchingBets(group, words)),
    ].where((r) => r.$2.isNotEmpty).toList()
      ..sort((a, b) =>
          b.$2.first.guaranteedProfit.compareTo(a.$2.first.guaranteedProfit));
    final available = results.where((r) => !r.$1.usesUsedBoost).toList();
    final usedUp = results.where((r) => r.$1.usesUsedBoost).toList();

    // Hidden while searching; a local final so Dart knows it's non-null below.
    final shownPlan =
        !isSearching && plan != null && plan.steps.isNotEmpty ? plan : null;
    final planGroupIds = plan?.groupIds ?? const <String>{};

    final children = <Widget>[
      if (shownPlan != null)
        BestPlanCard(
          plan: shownPlan,
          onPlaced: (bet) => _placeBet(
            bet,
            shownPlan.steps.expand((step) => step.boosts).toList(),
          ),
        ),
      if (shownPlan != null && available.isNotEmpty)
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 2),
          child: Text(
            'All hedges by boost combination',
            style: Theme.of(context).textTheme.titleSmall,
          ),
        ),
      for (final (group, bets) in available)
        HedgeGroupCard(
          key: ValueKey(group.id),
          group: group,
          bets: bets,
          isSearching: isSearching,
          inPlan: planGroupIds.contains(group.id),
          onPlaced: (bet) => _placeBet(bet, group.boosts),
        ),
      if (usedUp.isNotEmpty)
        Theme(
          data:
              Theme.of(context).copyWith(dividerColor: Colors.transparent),
          child: ExpansionTile(
            key: const PageStorageKey('used-hedges'),
            leading: Icon(Icons.inventory_2_outlined,
                color: Colors.grey.shade700),
            title: Text('Uses a used boost (${usedUp.length})'),
            subtitle: const Text('Hedges for boosts you\'ve marked used'),
            children: [
              for (final (group, bets) in usedUp)
                Opacity(
                  opacity: 0.6,
                  child: HedgeGroupCard(
                    key: ValueKey('used-${group.id}'),
                    group: group,
                    bets: bets,
                    isSearching: isSearching,
                  ),
                ),
            ],
          ),
        ),
    ];

    final emptyMessage = children.isNotEmpty
        ? null
        : isSearching
            ? 'No hedges match "${_query.trim()}".'
            : 'No hedges right now. They show up here when one of your '
                'active boosts lines up with current odds. Pull down to '
                'check again.';

    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
        padding: const EdgeInsets.symmetric(vertical: 6),
        children: emptyMessage == null
            ? children
            : [
                const SizedBox(height: 120),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 32),
                  child: Text(emptyMessage, textAlign: TextAlign.center),
                ),
              ],
      ),
    );
  }
}

class _StatusBar extends StatelessWidget {
  const _StatusBar({
    required this.status,
    required this.refreshing,
    required this.onRefresh,
  });

  final RefreshStatus? status;
  final bool refreshing;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final s = status;
    final ok = s?.ok ?? true;
    final lastRun = s?.lastRunAt;

    return Container(
      width: double.infinity,
      color: ok ? Colors.grey.shade100 : Colors.red.shade50,
      padding: const EdgeInsets.fromLTRB(16, 8, 4, 8),
      child: Row(
        children: [
          Icon(
            ok ? Icons.check_circle_outline : Icons.error_outline,
            color: ok ? Colors.green.shade700 : Colors.red.shade700,
            size: 20,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  lastRun == null
                      ? 'Not checked yet'
                      : 'Checked ${formatAgo(lastRun)}',
                  style: theme.textTheme.labelLarge,
                ),
                if (s != null && s.message.isNotEmpty)
                  Text(
                    s.message,
                    style: theme.textTheme.bodySmall,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                  ),
              ],
            ),
          ),
          if (refreshing)
            const Padding(
              padding: EdgeInsets.all(12),
              child: SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            )
          else
            IconButton(
              icon: const Icon(Icons.refresh),
              tooltip: 'Check for hedges now',
              onPressed: onRefresh,
            ),
        ],
      ),
    );
  }
}
