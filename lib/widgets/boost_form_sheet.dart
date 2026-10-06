import 'package:flutter/material.dart';

import '../models/profit_boost.dart';
import '../utils/format.dart';
import '../services/boost_repository.dart';
import 'game_picker.dart';
import 'league_picker.dart';

/// Bottom sheet for creating or editing a boost. Pops with the saved
/// [ProfitBoost], or null if the user dismisses it.
class BoostFormSheet extends StatefulWidget {
  const BoostFormSheet({
    super.key,
    required this.sportsbook,
    required this.loadGames,
    this.initial,
  });

  /// Loads upcoming games for the game picker.
  final Future<List<GameOption>> Function(BetType league) loadGames;

  /// Sportsbook to start with (the section the user tapped + in).
  final Sportsbook sportsbook;

  /// The boost being edited, or null when creating a new one.
  final ProfitBoost? initial;

  @override
  State<BoostFormSheet> createState() => _BoostFormSheetState();
}

class _BoostFormSheetState extends State<BoostFormSheet> {
  final _formKey = GlobalKey<FormState>();
  final _nicknameCtrl = TextEditingController();
  final _percentCtrl = TextEditingController();
  final _minOddsCtrl = TextEditingController();
  final _maxOddsCtrl = TextEditingController();
  final _maxBetCtrl = TextEditingController();

  late Sportsbook _sportsbook;
  BetType _betType = BetType.nfl;
  late DateTime _validFrom;
  late DateTime _validUntil;
  String? _dateError;

  // Single-game boosts: the game, or null for "any game".
  String? _eventId;
  String? _eventName;
  DateTime? _eventStart;

  bool get _isEditing => widget.initial != null;

  @override
  void initState() {
    super.initState();
    _sportsbook = widget.sportsbook;

    final boost = widget.initial;
    if (boost != null) {
      _sportsbook = boost.sportsbook;
      _betType = boost.betType;
      _validFrom = boost.validFrom;
      _validUntil = boost.validUntil;
      _nicknameCtrl.text = boost.nickname ?? '';
      _eventId = boost.eventId;
      _eventName = boost.eventName;
      _eventStart = boost.eventStart;
      _percentCtrl.text = _plainNumber(boost.percentBoost);
      _minOddsCtrl.text = formatOdds(boost.minOdds);
      _maxOddsCtrl.text = formatOdds(boost.maxOdds);
      _maxBetCtrl.text = _plainNumber(boost.maxBet);
    } else {
      final now = DateTime.now();
      _validFrom = DateTime(now.year, now.month, now.day, now.hour, now.minute);
      _validUntil = DateTime(now.year, now.month, now.day, 23, 59);
    }
  }

  @override
  void dispose() {
    _nicknameCtrl.dispose();
    _percentCtrl.dispose();
    _minOddsCtrl.dispose();
    _maxOddsCtrl.dispose();
    _maxBetCtrl.dispose();
    super.dispose();
  }

  /// 25.0 -> "25", 37.5 -> "37.5"
  static String _plainNumber(double value) => value == value.roundToDouble()
      ? value.toStringAsFixed(0)
      : value.toString();

  // Accepts "-110", "+150", or "150".
  int? _parseOdds(String? value) =>
      int.tryParse((value ?? '').trim().replaceAll('+', ''));

  String? _validateOdds(String? value) {
    final odds = _parseOdds(value);
    if (odds == null) return 'Enter odds like -110 or +150';
    if (odds.abs() < 100) return 'Use -100 or lower, or +100 or higher';
    return null;
  }

  String? _validateMaxOdds(String? value) {
    final basic = _validateOdds(value);
    if (basic != null) return basic;
    final min = _parseOdds(_minOddsCtrl.text);
    if (min != null && _parseOdds(value)! < min) {
      return 'Max odds must be at least min odds';
    }
    return null;
  }

  Future<void> _pickLeague() async {
    final league = await showLeaguePicker(context, _betType);
    if (league == null || !mounted || league == _betType) return;
    setState(() {
      _betType = league;
      // A game belongs to one league, so changing the league clears it.
      _eventId = null;
      _eventName = null;
      _eventStart = null;
    });
  }

  Future<void> _pickDateTime({required bool isStart}) async {
    final initial = isStart ? _validFrom : _validUntil;

    final date = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: DateTime(2020),
      lastDate: DateTime(2100),
    );
    if (date == null || !mounted) return;

    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(initial),
    );
    if (time == null || !mounted) return;

    final picked =
        DateTime(date.year, date.month, date.day, time.hour, time.minute);
    setState(() {
      if (isStart) {
        _validFrom = picked;
      } else {
        _validUntil = picked;
      }
      _dateError = null;
    });
  }

  Future<void> _pickGame() async {
    final choice = await showGamePicker(
      context: context,
      league: _betType,
      loadGames: widget.loadGames,
      selectedId: _eventId,
    );
    if (choice == null || !mounted) return;
    setState(() {
      _eventId = choice.game?.id;
      _eventName = choice.game?.name;
      _eventStart = choice.game?.commenceTime;
      _dateError = null;
    });
  }

  void _save() {
    final fieldsValid = _formKey.currentState!.validate();
    String? dateError;
    if (!_validUntil.isAfter(_validFrom)) {
      dateError = 'End must be after start';
    } else if (_eventStart != null &&
        (_eventStart!.isBefore(_validFrom) || _eventStart!.isAfter(_validUntil))) {
      dateError = 'The game starts ${formatDateTime(_eventStart!)}, outside this '
          'boost\'s window. Adjust the window or pick another game.';
    }
    setState(() => _dateError = dateError);
    if (!fieldsValid || dateError != null) return;

    final nickname = _nicknameCtrl.text.trim();
    Navigator.of(context).pop(
      ProfitBoost(
        id: widget.initial?.id ??
            DateTime.now().microsecondsSinceEpoch.toString(),
        sportsbook: _sportsbook,
        nickname: nickname.isEmpty ? null : nickname,
        eventId: _eventId,
        eventName: _eventName,
        eventStart: _eventStart,
        percentBoost: double.parse(_percentCtrl.text.trim()),
        betType: _betType,
        minOdds: _parseOdds(_minOddsCtrl.text)!,
        maxOdds: _parseOdds(_maxOddsCtrl.text)!,
        validFrom: _validFrom,
        validUntil: _validUntil,
        maxBet: double.parse(_maxBetCtrl.text.trim()),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final keyboardHeight = MediaQuery.of(context).viewInsets.bottom;
    const signedNumber = TextInputType.numberWithOptions(signed: true);
    const decimalNumber = TextInputType.numberWithOptions(decimal: true);

    return Padding(
      padding: EdgeInsets.fromLTRB(20, 0, 20, keyboardHeight + 20),
      child: Form(
        key: _formKey,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                _isEditing ? 'Edit boost' : 'New boost',
                style: theme.textTheme.titleLarge,
              ),
              const SizedBox(height: 16),
              SegmentedButton<Sportsbook>(
                segments: [
                  for (final book in Sportsbook.values)
                    ButtonSegment(value: book, label: Text(book.label)),
                ],
                selected: {_sportsbook},
                onSelectionChanged: (selection) =>
                    setState(() => _sportsbook = selection.first),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _nicknameCtrl,
                textCapitalization: TextCapitalization.sentences,
                maxLength: 40,
                decoration: const InputDecoration(
                  labelText: 'Nickname (optional)',
                  hintText: 'e.g. Sunday NFL boost',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 8),
              TextFormField(
                controller: _percentCtrl,
                keyboardType: decimalNumber,
                decoration: const InputDecoration(
                  labelText: 'Percent boost',
                  suffixText: '%',
                  border: OutlineInputBorder(),
                ),
                validator: (v) {
                  final p = double.tryParse((v ?? '').trim());
                  return (p == null || p <= 0) ? 'Enter a boost above 0' : null;
                },
              ),
              const SizedBox(height: 16),
              _TapField(
                label: 'League',
                text: _betType.label,
                icon: Icons.expand_more,
                onTap: _pickLeague,
                helperText: _betType.autoMatched
                    ? null
                    : 'Saved, but hedges aren\'t searched for this league.',
              ),
              const SizedBox(height: 16),
              _TapField(
                label: 'Game',
                text: _eventName == null
                    ? 'Any game'
                    : '$_eventName, ${formatDateTime(_eventStart ?? DateTime.now())}',
                icon: Icons.sports_score,
                onTap: _betType.autoMatched ? _pickGame : null,
                helperText: _eventName == null
                    ? 'Pick a game if this boost is only for one game.'
                    : 'Only bets on this game will use the boost.',
              ),
              const SizedBox(height: 16),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: TextFormField(
                      controller: _minOddsCtrl,
                      keyboardType: signedNumber,
                      decoration: const InputDecoration(
                        labelText: 'Min odds',
                        hintText: '-200',
                        border: OutlineInputBorder(),
                        errorMaxLines: 2,
                      ),
                      validator: _validateOdds,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: TextFormField(
                      controller: _maxOddsCtrl,
                      keyboardType: signedNumber,
                      decoration: const InputDecoration(
                        labelText: 'Max odds',
                        hintText: '+300',
                        border: OutlineInputBorder(),
                        errorMaxLines: 2,
                      ),
                      validator: _validateMaxOdds,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              _TapField(
                label: 'Valid from',
                text: '${formatDateTime(_validFrom)}, ${_validFrom.year}',
                icon: Icons.event,
                onTap: () => _pickDateTime(isStart: true),
              ),
              const SizedBox(height: 12),
              _TapField(
                label: 'Valid until',
                text: '${formatDateTime(_validUntil)}, ${_validUntil.year}',
                icon: Icons.event,
                errorText: _dateError,
                onTap: () => _pickDateTime(isStart: false),
              ),
              const SizedBox(height: 16),
              TextFormField(
                controller: _maxBetCtrl,
                keyboardType: decimalNumber,
                decoration: const InputDecoration(
                  labelText: 'Max bet amount',
                  prefixText: '\$ ',
                  border: OutlineInputBorder(),
                ),
                validator: (v) {
                  final amount = double.tryParse((v ?? '').trim());
                  return (amount == null || amount <= 0)
                      ? 'Enter an amount above 0'
                      : null;
                },
              ),
              const SizedBox(height: 24),
              FilledButton(
                onPressed: _save,
                style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14),
                ),
                child: Text(_isEditing ? 'Save changes' : 'Add boost'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// A tappable field that looks like a text input (for pickers).
class _TapField extends StatelessWidget {
  const _TapField({
    required this.label,
    required this.text,
    required this.icon,
    required this.onTap,
    this.errorText,
    this.helperText,
  });

  final String label;
  final String text;
  final IconData icon;
  final VoidCallback? onTap;
  final String? errorText;
  final String? helperText;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(4),
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          errorText: errorText,
          helperText: helperText,
          helperMaxLines: 2,
          errorMaxLines: 3,
          border: const OutlineInputBorder(),
          suffixIcon: Icon(icon),
        ),
        child: Text(text),
      ),
    );
  }
}
