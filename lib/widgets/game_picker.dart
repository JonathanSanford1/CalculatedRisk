import 'package:flutter/material.dart';

import '../models/profit_boost.dart';
import '../services/boost_repository.dart';
import '../utils/format.dart';

/// The user's answer: a game, or a null game for "Any game".
class GameChoice {
  const GameChoice(this.game);
  final GameOption? game;
}

/// Lists upcoming games in [league] for single-game boosts.
/// Returns null if dismissed.
Future<GameChoice?> showGamePicker({
  required BuildContext context,
  required BetType league,
  required Future<List<GameOption>> Function(BetType league) loadGames,
  String? selectedId,
}) {
  return showModalBottomSheet<GameChoice>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    showDragHandle: true,
    builder: (_) => _GamePicker(league: league, loadGames: loadGames, selectedId: selectedId),
  );
}

class _GamePicker extends StatefulWidget {
  const _GamePicker({required this.league, required this.loadGames, this.selectedId});

  final BetType league;
  final Future<List<GameOption>> Function(BetType league) loadGames;
  final String? selectedId;

  @override
  State<_GamePicker> createState() => _GamePickerState();
}

class _GamePickerState extends State<_GamePicker> {
  late Future<List<GameOption>> _games;
  String _query = '';

  @override
  void initState() {
    super.initState();
    _games = widget.loadGames(widget.league);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return SizedBox(
      height: MediaQuery.of(context).size.height * 0.8,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Which game is this boost for?', style: theme.textTheme.titleLarge),
                Text(widget.league.label, style: theme.textTheme.bodyMedium),
                const SizedBox(height: 8),
                TextField(
                  decoration: const InputDecoration(
                    hintText: 'Search teams',
                    prefixIcon: Icon(Icons.search),
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                  onChanged: (value) => setState(() => _query = value.trim().toLowerCase()),
                ),
              ],
            ),
          ),
          ListTile(
            leading: const Icon(Icons.all_inclusive),
            title: const Text('Any game'),
            subtitle: const Text('The boost works on any game in this league'),
            trailing: widget.selectedId == null ? const Icon(Icons.check) : null,
            onTap: () => Navigator.of(context).pop(const GameChoice(null)),
          ),
          const Divider(height: 1),
          Expanded(
            child: FutureBuilder<List<GameOption>>(
              future: _games,
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text('Couldn\'t load games.\n${snapshot.error}',
                              textAlign: TextAlign.center),
                          const SizedBox(height: 12),
                          FilledButton(
                            onPressed: () => setState(() {
                              _games = widget.loadGames(widget.league);
                            }),
                            child: const Text('Try again'),
                          ),
                        ],
                      ),
                    ),
                  );
                }
                if (!snapshot.hasData) {
                  return const Center(child: CircularProgressIndicator());
                }
                final games = snapshot.data!
                    .where((g) => _query.isEmpty || g.name.toLowerCase().contains(_query))
                    .toList();
                if (games.isEmpty) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(
                        snapshot.data!.isEmpty
                            ? 'No upcoming games in this league right now.'
                            : 'No games match your search.',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  );
                }
                return ListView.separated(
                  itemCount: games.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (_, i) {
                    final game = games[i];
                    return ListTile(
                      title: Text(game.name),
                      subtitle: Text(formatDateTime(game.commenceTime)),
                      trailing: game.id == widget.selectedId ? const Icon(Icons.check) : null,
                      onTap: () => Navigator.of(context).pop(GameChoice(game)),
                    );
                  },
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}
