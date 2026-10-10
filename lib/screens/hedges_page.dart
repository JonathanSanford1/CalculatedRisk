import 'dart:async';

import 'package:cloud_functions/cloud_functions.dart';
import 'package:flutter/material.dart';

import '../models/best_plan.dart';
import '../models/hedge_goal.dart';
import '../models/opportunity.dart';
import '../models/rounding.dart';
import '../services/boost_repository.dart';
import '../utils/format.dart';
import '../widgets/error_view.dart';
import '../widgets/opportunity_card.dart';
import '../widgets/plan_card.dart';

/// Hedges found by the Cloud Function: the best plan first, then one card per
/// boost combination, with a search bar. The options sheet, next to the search
/// bar, sets whether everything is ranked by guaranteed profit or by possible
/// profit. Cards for boosts whose window
/// hasn't opened come next, and cards using a used boost are collected at the
/// bottom.
class HedgesPage extends StatefulWidget {
  const HedgesPage({super.key, required this.repository});

  final BoostRepository repository;

  @override
  State<HedgesPage> createState() => _HedgesPageState();
}

class _HedgesPageState extends State<HedgesPage> {
  /// The search field's height: 0.8 of the 48 px it would otherwise be.
  static const double _searchHeight = kMinInteractiveDimension * 0.8;

  final _searchCtrl = TextEditingController();
  late Stream<List<HedgeGroup>> _groupStream;
  late Stream<RefreshStatus?> _statusStream;
  late Stream<BestPlanSet?> _planStream;
  StreamSubscription<Preferences>? _prefsSubscription;
  RoundingMode _mode = RoundingMode.small;
  HedgeGoal _goal = HedgeGoal.guaranteed;
  bool _allowSameBook = false;
  bool _refreshing = false;
  String _query = '';

  /// Bumped whenever a setting shown in the options sheet changes. The sheet is
  /// a separate route, so rebuilding this page alone doesn't redraw it.
  final _sheetChanges = ValueNotifier<int>(0);

  @override
  void initState() {
    super.initState();
    _groupStream = widget.repository.watchHedgeGroups();
    _statusStream = widget.repository.watchStatus();
    _planStream = widget.repository.watchPlans();
    // Keep the settings in sync with what's saved (also across devices).
    _prefsSubscription = widget.repository.watchPreferences().listen(
      (prefs) {
        if (!mounted) return;
        setState(() {
          _mode = prefs.roundingMode;
          _goal = prefs.goal;
          _allowSameBook = prefs.allowSameBook;
        });
        _notifySheet();
      },
      onError: (_) {}, // keep the current settings if they can't be read
    );
  }

  @override
  void dispose() {
    _prefsSubscription?.cancel();
    _searchCtrl.dispose();
    _sheetChanges.dispose();
    super.dispose();
  }

  void _showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  /// Redraws the options sheet if it's open.
  void _notifySheet() => _sheetChanges.value++;

  void _selectGoal(HedgeGoal goal) {
    setState(() => _goal = goal); // switch immediately
    _notifySheet();
    widget.repository.setGoal(goal).catchError((Object e) {
      _showMessage('Couldn\'t save the ranking choice: $e');
    });
  }

  void _selectMode(RoundingMode mode) {
    setState(() => _mode = mode); // switch immediately
    _notifySheet();
    widget.repository.setRoundingMode(mode).catchError((Object e) {
      _showMessage('Couldn\'t save the rounding choice: $e');
    });
  }

  /// Same-sportsbook hedges change which hedges exist, so this recalculates.
  Future<void> _setSameBook(bool allow) async {
    setState(() {
      _allowSameBook = allow;
      _refreshing = true;
    });
    _notifySheet();
    try {
      await widget.repository.setAllowSameBook(allow);
      _showMessage(allow
          ? 'Same-sportsbook hedges are on. Hedges updated.'
          : 'Same-sportsbook hedges are off. Hedges updated.');
    } on FirebaseFunctionsException catch (e) {
      _showMessage(e.message ?? 'Saved, but the update failed (${e.code}). Pull to refresh.');
    } catch (e) {
      _showMessage('Couldn\'t change the setting: $e');
    } finally {
      if (mounted) {
        setState(() => _refreshing = false);
        _notifySheet();
      }
    }
  }

  void _openOptions() {
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      useSafeArea: true,
      isScrollControlled: true,
      builder: (sheetContext) => ValueListenableBuilder<int>(
        valueListenable: _sheetChanges,
        builder: (sheetContext, _, _) {
          final theme = Theme.of(sheetContext);
          return SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Hedge options', style: theme.textTheme.titleLarge),
                const SizedBox(height: 16),
                Text('Rank hedges by', style: theme.textTheme.labelLarge),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: SegmentedButton<HedgeGoal>(
                    showSelectedIcon: false,
                    segments: [
                      for (final g in HedgeGoal.values)
                        ButtonSegment(value: g, label: Text(g.label)),
                    ],
                    selected: {_goal},
                    onSelectionChanged: (selection) => _selectGoal(selection.first),
                  ),
                ),
                const SizedBox(height: 4),
                Text(_goal.description, style: theme.textTheme.bodySmall),
                const SizedBox(height: 16),
                Text('Bet rounding', style: theme.textTheme.labelLarge),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: SegmentedButton<RoundingMode>(
                    showSelectedIcon: false,
                    segments: [
                      for (final m in RoundingMode.values)
                        ButtonSegment(value: m, label: Text(m.label)),
                    ],
                    selected: {_mode},
                    onSelectionChanged: (selection) => _selectMode(selection.first),
                  ),
                ),
                const SizedBox(height: 4),
                Text(_mode.description, style: theme.textTheme.bodySmall),
                const SizedBox(height: 16),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Allow hedges at the same sportsbook'),
                  subtitle: const Text(
                    'Lets both bets be placed at one sportsbook. More hedges, '
                    'but betting both sides at one book is easier to notice.',
                  ),
                  value: _allowSameBook,
                  // Off while hedges are being recalculated, so a second tap
                  // can't start another recalculation (each one uses API credits).
                  onChanged: _refreshing ? null : (value) => _setSameBook(value),
                ),
                if (_refreshing) ...[
                  const SizedBox(height: 4),
                  const LinearProgressIndicator(),
                  const SizedBox(height: 6),
                  Text('Updating hedges…', style: theme.textTheme.bodySmall),
                ],
              ],
            ),
          );
        },
      ),
    );
  }

  /// Runs the Cloud Function now. New results arrive through the streams.
  Future<void> _refresh() async {
    if (_refreshing) return;
    setState(() => _refreshing = true);
    _notifySheet();
    try {
      _showMessage(await widget.repository.refreshNow());
    } on FirebaseFunctionsException catch (e) {
      _showMessage(e.message ?? 'Refresh failed (${e.code}).');
    } catch (e) {
      _showMessage('Refresh failed: $e');
    } finally {
      if (mounted) {
        setState(() => _refreshing = false);
        _notifySheet();
      }
    }
  }

  /// "I placed this": marks the bet's boost(s) used and records the hedge,
  /// with the amounts of the version that was showing.
  Future<void> _placeBet(
      Opportunity bet, HedgeVersion version, List<GroupBoost> boosts) async {
    final used = boosts.where((b) => bet.boostIds.contains(b.id)).toList();
    final names = used.map((b) => '${b.sportsbook.label} ${b.title}').join(' and ');
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Mark this hedge as placed?'),
        content: Text(
          '$names will be marked used, and this hedge (${version.label.toLowerCase()} '
          'amounts: guaranteed +${formatCents(version.guaranteedProfit)}, up to '
          '+${formatCents(version.maxProfit)}) will be saved on '
          '${used.length == 1 ? 'it' : 'them'}. You can undo this from the Boosts tab.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Mark placed')),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await widget.repository.markPlaced(bet, version);
      _showMessage('Marked as placed. The plan will update in a moment.');
    } catch (e) {
      _showMessage('Couldn\'t mark the hedge placed: $e');
    }
  }

  /// Bets in [bets] matching the search. Every word must match somewhere.
  /// Searching a boost's name, book, league, or game shows all of its bets.
  List<Opportunity> _matchingBets(
      HedgeGroup group, List<Opportunity> bets, List<String> words) {
    if (words.isEmpty) return bets;
    bool matches(String text) => words.every(text.contains);
    if (matches(group.boostSearchText)) return bets;
    return bets
        .where((bet) => matches('${group.boostSearchText} ${bet.searchText}'))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
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
        // Search, plus options (ranking, rounding, and same-sportsbook hedges).
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 4, 0),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _searchCtrl,
                  textInputAction: TextInputAction.search,
                  decoration: InputDecoration(
                    hintText: 'Search teams, players, boosts',
                    prefixIcon: const Icon(Icons.search, size: 20),
                    suffixIcon: _query.isEmpty
                        ? null
                        : Tooltip(
                            message: 'Clear search',
                            child: InkResponse(
                              radius: 18,
                              onTap: () {
                                _searchCtrl.clear();
                                setState(() => _query = '');
                              },
                              child: const Icon(Icons.clear, size: 20),
                            ),
                          ),
                    // Icons are 48 px tall by default, and that sets the
                    // field's height, so they're shrunk to let it be shorter.
                    prefixIconConstraints: const BoxConstraints(
                        minWidth: 40, minHeight: _searchHeight),
                    suffixIconConstraints: const BoxConstraints(
                        minWidth: 40, minHeight: _searchHeight),
                    isDense: true,
                    contentPadding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                    border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(24)),
                  ),
                  onChanged: (value) => setState(() => _query = value),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.tune),
                tooltip: 'Hedge options',
                onPressed: _openOptions,
              ),
            ],
          ),
        ),
        // The current ranking and options, since the ranking is no longer
        // visible on the screen itself.
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 2, 16, 4),
          child: Align(
            alignment: Alignment.centerLeft,
            child: Text(
              '${_goal.description} ${_mode.label} rounding, '
              'same-sportsbook hedges ${_allowSameBook ? 'on' : 'off'}.',
              style: theme.textTheme.bodySmall,
            ),
          ),
        ),
        Expanded(
          child: StreamBuilder<BestPlanSet?>(
            stream: _planStream,
            builder: (context, planSnapshot) => StreamBuilder<List<HedgeGroup>>(
              stream: _groupStream,
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  return ErrorView(
                    message: 'Couldn\'t load hedges.',
                    details: '${snapshot.error}',
                    onRetry: () => setState(
                        () => _groupStream = widget.repository.watchHedgeGroups()),
                  );
                }
                if (!snapshot.hasData) {
                  return const Center(child: CircularProgressIndicator());
                }
                final groups = [
                  for (final group in snapshot.data!)
                    if (group.withMode(_mode).bets.isNotEmpty) group.withMode(_mode),
                ];
                return _buildList(groups, planSnapshot.data?.planFor(_mode, _goal));
              },
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildList(List<HedgeGroup> groups, BestPlan? plan) {
    final theme = Theme.of(context);
    final words = _query
        .toLowerCase()
        .split(RegExp(r'\s+'))
        .where((w) => w.isNotEmpty)
        .toList();
    final isSearching = words.isNotEmpty;

    // Each group with its bets for the goal (and search), best group first.
    final results = <(HedgeGroup, List<Opportunity>)>[
      for (final group in groups) (group, _matchingBets(group, group.betsFor(_goal), words)),
    ].where((r) => r.$2.isNotEmpty).toList()
      ..sort((a, b) => b.$2.first.valueFor(_goal).compareTo(a.$2.first.valueFor(_goal)));
    final available =
        results.where((r) => !r.$1.usesUsedBoost && !r.$1.upcoming).toList();
    final opening =
        results.where((r) => !r.$1.usesUsedBoost && r.$1.upcoming).toList();
    final usedUp = results.where((r) => r.$1.usesUsedBoost).toList();

    // Hidden while searching; a local final so Dart knows it's non-null below.
    final shownPlan =
        !isSearching && plan != null && plan.steps.isNotEmpty ? plan : null;
    final planGroupIds = plan?.groupIds ?? const <String>{};

    Widget card((HedgeGroup, List<Opportunity>) r, {bool placeable = true}) =>
        HedgeGroupCard(
          key: ValueKey('${r.$1.id}-${_goal.name}'),
          group: r.$1,
          bets: r.$2,
          isSearching: isSearching,
          goal: _goal,
          inPlan: planGroupIds.contains(r.$1.id),
          onPlaced: placeable
              ? (bet, version) => _placeBet(bet, version, r.$1.boosts)
              : null,
        );

    Widget heading(String text) => Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 2),
          child: Text(text, style: theme.textTheme.titleSmall),
        );

    final children = <Widget>[
      if (shownPlan != null)
        BestPlanCard(
          plan: shownPlan,
          onPlaced: (bet, version) => _placeBet(
              bet, version, shownPlan.steps.expand((s) => s.boosts).toList()),
        ),
      if (shownPlan != null && available.isNotEmpty)
        heading('All hedges by boost combination'),
      for (final r in available) card(r),
      if (opening.isNotEmpty) heading('Boosts that open later'),
      for (final r in opening) card(r, placeable: false),
      if (usedUp.isNotEmpty)
        Theme(
          data: theme.copyWith(dividerColor: Colors.transparent),
          child: ExpansionTile(
            key: const PageStorageKey('used-hedges'),
            leading: Icon(Icons.inventory_2_outlined, color: Colors.grey.shade700),
            title: Text('Uses a used boost (${usedUp.length})'),
            subtitle: const Text('Hedges for boosts you\'ve marked used'),
            children: [
              for (final r in usedUp)
                Opacity(opacity: 0.6, child: card(r, placeable: false)),
            ],
          ),
        ),
    ];

    final emptyMessage = children.isNotEmpty
        ? null
        : isSearching
            ? 'No hedges match "${_query.trim()}".'
            : 'No hedges right now. They show up here when one of your boosts '
                'lines up with current odds. The status above explains what was '
                'checked. Pull down to check again.';

    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
        padding: const EdgeInsets.symmetric(vertical: 6),
        children: emptyMessage == null
            ? children
            : [
                const SizedBox(height: 100),
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
      padding: const EdgeInsets.fromLTRB(16, 8, 4, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
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
                        maxLines: 8,
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
          const SizedBox(height: 4),
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: _CreditsBar(
              remaining: s?.quotaRemaining,
              total: s?.quotaTotal,
            ),
          ),
        ],
      ),
    );
  }
}

/// How many Odds API credits are left this month, as a colored bar.
class _CreditsBar extends StatelessWidget {
  const _CreditsBar({required this.remaining, required this.total});

  final int? remaining;
  final int? total;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final left = remaining;
    final size = total;

    if (left == null || size == null || size <= 0) {
      return Row(
        children: [
          Icon(Icons.data_usage, size: 16, color: Colors.grey.shade600),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              'API credits appear after the next odds check.',
              style: theme.textTheme.bodySmall,
            ),
          ),
        ],
      );
    }

    final fraction = (left / size).clamp(0.0, 1.0);
    final color = fraction > 0.4
        ? Colors.green.shade600
        : fraction > 0.15
            ? Colors.amber.shade700
            : Colors.red.shade600;

    return Row(
      children: [
        Icon(Icons.data_usage, size: 16, color: color),
        const SizedBox(width: 6),
        Text('API credits', style: theme.textTheme.labelMedium),
        const SizedBox(width: 10),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: fraction,
              minHeight: 8,
              color: color,
              backgroundColor: Colors.grey.shade300,
            ),
          ),
        ),
        const SizedBox(width: 10),
        Text(
          '$left of $size left',
          style: theme.textTheme.labelMedium?.copyWith(
            color: fraction > 0.15 ? null : Colors.red.shade700,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}