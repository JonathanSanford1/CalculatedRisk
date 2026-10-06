import 'package:cloud_firestore/cloud_firestore.dart';

import '../utils/format.dart';
import 'firestore_dates.dart';
import 'opportunity.dart';

/// The sportsbook a boost belongs to. [name] ("draftkings", "fanduel") is what
/// gets stored in Firestore and matches The Odds API's bookmaker keys.
enum Sportsbook {
  draftkings('DraftKings'),
  fanduel('FanDuel');

  const Sportsbook(this.label);
  final String label;
}

/// Groups leagues in the league picker.
enum SportCategory {
  football('Football'),
  basketball('Basketball'),
  baseball('Baseball'),
  hockey('Hockey'),
  soccer('Soccer'),
  tennis('Tennis'),
  golf('Golf'),
  combat('Combat sports'),
  other('Other sports');

  const SportCategory(this.label);
  final String label;
}

/// Leagues a boost can apply to. [name] is stored in Firestore and must match
/// a key in LEAGUE_KEYS in functions/main.py for hedges to be searched.
/// Leagues with [autoMatched] false are saved but not checked for hedges.
enum BetType {
  // Football
  nfl('NFL', SportCategory.football),
  ncaaf('College Football (NCAAF)', SportCategory.football),
  cfl('CFL', SportCategory.football),
  ufl('UFL', SportCategory.football),
  // Basketball
  nba('NBA', SportCategory.basketball),
  wnba('WNBA', SportCategory.basketball),
  ncaab('Men\'s College Basketball', SportCategory.basketball),
  wncaab('Women\'s College Basketball', SportCategory.basketball),
  euroleague('EuroLeague', SportCategory.basketball),
  // Baseball
  mlb('MLB', SportCategory.baseball),
  collegeBaseball('College Baseball', SportCategory.baseball),
  kbo('KBO (Korea)', SportCategory.baseball),
  npb('NPB (Japan)', SportCategory.baseball),
  // Hockey
  nhl('NHL', SportCategory.hockey),
  ahl('AHL', SportCategory.hockey),
  shl('SHL (Sweden)', SportCategory.hockey),
  liiga('Liiga (Finland)', SportCategory.hockey),
  // Soccer
  epl('Premier League', SportCategory.soccer),
  efl('EFL Championship', SportCategory.soccer),
  laLiga('La Liga', SportCategory.soccer),
  serieA('Serie A', SportCategory.soccer),
  bundesliga('Bundesliga', SportCategory.soccer),
  ligue1('Ligue 1', SportCategory.soccer),
  ucl('Champions League', SportCategory.soccer),
  uel('Europa League', SportCategory.soccer),
  mls('MLS', SportCategory.soccer),
  ligaMx('Liga MX', SportCategory.soccer),
  eredivisie('Eredivisie', SportCategory.soccer),
  worldCup('World Cup', SportCategory.soccer),
  soccer('Other soccer league', SportCategory.soccer, autoMatched: false),
  // Tennis
  atp('ATP (men\'s tennis)', SportCategory.tennis),
  wta('WTA (women\'s tennis)', SportCategory.tennis),
  tennis('Any tennis match', SportCategory.tennis),
  // Golf: tournament-winner bets can't be hedged with two bets
  golf('Golf (PGA, LIV, majors)', SportCategory.golf, autoMatched: false),
  // Combat sports
  mma('UFC/MMA', SportCategory.combat),
  boxing('Boxing', SportCategory.combat),
  // Other
  afl('AFL (Aussie rules)', SportCategory.other),
  nrl('NRL (rugby league)', SportCategory.other),
  cricket('Cricket', SportCategory.other),
  motorsports('Motorsports (F1, NASCAR)', SportCategory.other,
      autoMatched: false),
  other('Other', SportCategory.other, autoMatched: false);

  const BetType(this.label, this.category, {this.autoMatched = true});
  final String label;
  final SportCategory category;
  final bool autoMatched;
}

enum BoostStatus { upcoming, active, expired }

class ProfitBoost {
  ProfitBoost({
    required this.id,
    required this.sportsbook,
    required this.percentBoost,
    required this.betType,
    required this.minOdds,
    required this.maxOdds,
    required this.validFrom,
    required this.validUntil,
    required this.maxBet,
    this.nickname,
    this.used = false,
    this.usedAt,
    this.placedBet,
    this.eventId,
    this.eventName,
    this.eventStart,
  });

  final String id;
  final Sportsbook sportsbook;
  final double percentBoost; // 25 means +25%
  final BetType betType;
  final int minOdds; // American odds, e.g. -200
  final int maxOdds; // American odds, e.g. +300
  final DateTime validFrom;
  final DateTime validUntil;
  final double maxBet; // dollars
  final String? nickname; // optional, e.g. "Sunday NFL boost"
  final bool used; // the user marked this boost as already used
  final DateTime? usedAt;
  final Opportunity? placedBet; // the hedge the user placed, if recorded

  /// Set when the boost is for one specific game ("Any bet on Bills @ Chiefs").
  final String? eventId;
  final String? eventName;
  final DateTime? eventStart;

  bool get isSingleGame => eventId != null && eventId!.isNotEmpty;

  bool get hasNickname => nickname != null && nickname!.trim().isNotEmpty;

  /// "+25% NFL"
  String get shortLabel => '+${formatPercent(percentBoost)} ${betType.label}';

  BoostStatus statusAt(DateTime now) {
    if (now.isBefore(validFrom)) return BoostStatus.upcoming;
    if (now.isAfter(validUntil)) return BoostStatus.expired;
    return BoostStatus.active;
  }

  /// Field names here must match what functions/main.py reads.
  /// Dates are stored as Firestore Timestamps, which keep the timezone.
  /// "used" and "placedBet" aren't written here, so editing a boost keeps them
  /// (they're changed only by the repository's mark-used methods).
  Map<String, dynamic> toFirestore({required bool isNew}) => {
        'bookmaker': sportsbook.name,
        'percentBoost': percentBoost,
        'betType': betType.name,
        'minOdds': minOdds,
        'maxOdds': maxOdds,
        'validFrom': Timestamp.fromDate(validFrom),
        'validUntil': Timestamp.fromDate(validUntil),
        'maxBet': maxBet,
        'nickname': hasNickname ? nickname!.trim() : null,
        'eventId': isSingleGame ? eventId : null,
        'eventName': isSingleGame ? eventName : null,
        'eventStart': isSingleGame && eventStart != null
            ? Timestamp.fromDate(eventStart!)
            : null,
        'updatedAt': FieldValue.serverTimestamp(),
        if (isNew) 'createdAt': FieldValue.serverTimestamp(),
      };

  factory ProfitBoost.fromFirestore(String id, Map<String, dynamic> data) {
    // Early test boosts stored a "section" color instead of a sportsbook.
    final bookKey = data['bookmaker'] as String? ??
        (data['section'] == 'blue' ? 'fanduel' : 'draftkings');

    return ProfitBoost(
      id: id,
      sportsbook:
          Sportsbook.values.asNameMap()[bookKey] ?? Sportsbook.draftkings,
      percentBoost: (data['percentBoost'] as num? ?? 0).toDouble(),
      betType: BetType.values.asNameMap()[data['betType']] ?? BetType.other,
      minOdds: (data['minOdds'] as num? ?? -100).toInt(),
      maxOdds: (data['maxOdds'] as num? ?? 100).toInt(),
      validFrom: readDate(data['validFrom']),
      validUntil: readDate(data['validUntil']),
      maxBet: (data['maxBet'] as num? ?? 0).toDouble(),
      nickname: data['nickname'] as String?,
      used: data['used'] as bool? ?? false,
      usedAt: data['usedAt'] == null ? null : readDate(data['usedAt']),
      placedBet: data['placedBet'] == null
          ? null
          : Opportunity.fromMap(
              Map<String, dynamic>.from(data['placedBet'] as Map)),
      eventId: data['eventId'] as String?,
      eventName: data['eventName'] as String?,
      eventStart:
          data['eventStart'] == null ? null : readDate(data['eventStart']),
    );
  }
}
