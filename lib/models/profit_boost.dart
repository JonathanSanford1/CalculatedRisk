/// Which half of the screen a boost lives in.
enum BoostSection { green, blue }

/// Sports / leagues a boost can apply to. Add or remove entries freely.
enum BetType {
  nfl('NFL'),
  nba('NBA'),
  mlb('MLB'),
  nhl('NHL'),
  ncaaf('NCAAF'),
  ncaab('NCAAB'),
  soccer('Soccer'),
  tennis('Tennis'),
  golf('Golf'),
  mma('UFC/MMA'),
  other('Other');

  const BetType(this.label);
  final String label;
}

enum BoostStatus { upcoming, active, expired }

class ProfitBoost {
  ProfitBoost({
    required this.id,
    required this.section,
    required this.percentBoost,
    required this.betType,
    required this.minOdds,
    required this.maxOdds,
    required this.validFrom,
    required this.validUntil,
    required this.maxBet,
  });

  final String id;
  final BoostSection section;
  final double percentBoost; // e.g. 25 means +25%
  final BetType betType;
  final int minOdds; // American odds, e.g. -200
  final int maxOdds; // American odds, e.g. +300
  final DateTime validFrom;
  final DateTime validUntil;
  final double maxBet; // dollars

  BoostStatus statusAt(DateTime now) {
    if (now.isBefore(validFrom)) return BoostStatus.upcoming;
    if (now.isAfter(validUntil)) return BoostStatus.expired;
    return BoostStatus.active;
  }

  /// JSON helpers: useful later for saving to the device
  /// or sending boosts to your Python backend.
  Map<String, dynamic> toJson() => {
        'id': id,
        'section': section.name,
        'percentBoost': percentBoost,
        'betType': betType.name,
        'minOdds': minOdds,
        'maxOdds': maxOdds,
        'validFrom': validFrom.toIso8601String(),
        'validUntil': validUntil.toIso8601String(),
        'maxBet': maxBet,
      };

  factory ProfitBoost.fromJson(Map<String, dynamic> json) => ProfitBoost(
        id: json['id'] as String,
        section: BoostSection.values.byName(json['section'] as String),
        percentBoost: (json['percentBoost'] as num).toDouble(),
        betType: BetType.values.byName(json['betType'] as String),
        minOdds: json['minOdds'] as int,
        maxOdds: json['maxOdds'] as int,
        validFrom: DateTime.parse(json['validFrom'] as String),
        validUntil: DateTime.parse(json['validUntil'] as String),
        maxBet: (json['maxBet'] as num).toDouble(),
      );
}