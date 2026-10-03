import 'package:flutter/material.dart';

import '../models/profit_boost.dart';
import '../utils/format.dart';

/// Bottom sheet for creating a boost. Pops with a [ProfitBoost] on save,
/// or null if the user dismisses it.
class BoostFormSheet extends StatefulWidget {
  const BoostFormSheet({super.key, required this.section});

  final BoostSection section;

  @override
  State<BoostFormSheet> createState() => _BoostFormSheetState();
}

class _BoostFormSheetState extends State<BoostFormSheet> {
  final _formKey = GlobalKey<FormState>();
  final _percentCtrl = TextEditingController();
  final _minOddsCtrl = TextEditingController();
  final _maxOddsCtrl = TextEditingController();
  final _maxBetCtrl = TextEditingController();

  BetType _betType = BetType.nfl;
  late DateTime _validFrom;
  late DateTime _validUntil;
  String? _dateError;

  @override
  void initState() {
    super.initState();
    final now = DateTime.now();
    _validFrom = DateTime(now.year, now.month, now.day, now.hour, now.minute);
    _validUntil = DateTime(now.year, now.month, now.day, 23, 59);
  }

  @override
  void dispose() {
    _percentCtrl.dispose();
    _minOddsCtrl.dispose();
    _maxOddsCtrl.dispose();
    _maxBetCtrl.dispose();
    super.dispose();
  }

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

  void _save() {
    final fieldsValid = _formKey.currentState!.validate();
    final datesValid = _validUntil.isAfter(_validFrom);
    setState(() => _dateError = datesValid ? null : 'End must be after start');
    if (!fieldsValid || !datesValid) return;

    Navigator.of(context).pop(
      ProfitBoost(
        id: DateTime.now().microsecondsSinceEpoch.toString(),
        section: widget.section,
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
    final sectionName =
        widget.section == BoostSection.green ? 'green' : 'blue';
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
              Text('New $sectionName boost', style: theme.textTheme.titleLarge),
              const SizedBox(height: 16),
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
              Text('Bet type', style: theme.textTheme.labelLarge),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 4,
                children: [
                  for (final type in BetType.values)
                    ChoiceChip(
                      label: Text(type.label),
                      selected: _betType == type,
                      onSelected: (_) => setState(() => _betType = type),
                    ),
                ],
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
              _DateTimeField(
                label: 'Valid from',
                value: _validFrom,
                onTap: () => _pickDateTime(isStart: true),
              ),
              const SizedBox(height: 12),
              _DateTimeField(
                label: 'Valid until',
                value: _validUntil,
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
                child: const Text('Add boost'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// A tappable field that looks like a text input and opens date + time pickers.
class _DateTimeField extends StatelessWidget {
  const _DateTimeField({
    required this.label,
    required this.value,
    required this.onTap,
    this.errorText,
  });

  final String label;
  final DateTime value;
  final VoidCallback onTap;
  final String? errorText;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(4),
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          errorText: errorText,
          border: const OutlineInputBorder(),
          suffixIcon: const Icon(Icons.event),
        ),
        child: Text('${formatDateTime(value)}, ${value.year}'),
      ),
    );
  }
}