import '../utils/format.dart';
import 'firestore_dates.dart';
import 'profit_boost.dart';
import 'rounding.dart';

/// One of the two bets in a hedge.
class OpportunityLeg {
  const OpportunityLeg({
    required this.sportsbook,
    required this.selection,
    required this.odds,
    required this.stake,
    required this.payout,
    required this.boostPercent,
    required this.boostId,
  });

  final Sportsbook sportsbook;
  final String selection; // e.g. "Kansas City Chiefs -3.5"
  final int odds;
  final double stake;
  final double payout; // total return if this bet wins, stake included
  final double boostPercent; // 0 when this leg isn't boosted
  final String? boostId; // which boost this leg uses, if any

  bool get isBoosted => boostPercent > 0;

  factory OpportunityLeg.fromMap(Map<String, dynamic> data) => OpportunityLeg(
        sportsbook: Sportsbook.values.asNameMap()[data['bookmaker']] ??
            Sportsbook.draftkings,
        selection: data['selection'] as String? ?? '',
        odds: (data['odds'] as num? ?? 0).toInt(),
        stake: (data['stake'] as num? ?? 0).toDouble(),
        payout: (data['payout'] as num? ?? 0).toDouble(),
        boostPercent: (data['boostPercent'] as num? ?? 0).toDouble(),
        boostId: data['boostId'] as String?,
      );
}

/// A single guaranteed-profit hedge: two bets to place.
class Opportunity {
  const Opportunity({
    required this.id,
    required this.betType,
    required this.game,
    required this.commenceTime,
    required this.legs,
    required this.totalStake,
    required this.guaranteedProfit,
    required this.roiPercent,
    required this.boostIds,
    required this.raw,
  });

  final String id;
  final BetType betType;
  final String game; // "Away @ Home"
  final DateTime commenceTime;
  final List<OpportunityLeg> legs;
  final double totalStake;
  final double guaranteedProfit;
  final double roiPercent;
  final List<String> boostIds; // the boost(s) this hedge uses
  final Map<String, dynamic> raw; // as stored, saved on boosts when placed

  /// Lowercase text the search bar matches against.
  String get searchText => [
        game,
        betType.label,
        betType.category.label,
        for (final leg in legs) ...[leg.selection, leg.sportsbook.label],
      ].join(' ').toLowerCase();

  factory Opportunity.fromMap(Map<String, dynamic> data) {
    final rawLegs = data['legs'] as List? ?? const [];
    return Opportunity(
      id: data['id'] as String? ?? '',
      betType: BetType.values.asNameMap()[data['betType']] ?? BetType.other,
      game: data['game'] as String? ?? '',
      commenceTime: readDate(data['commenceTime']),
      legs: [
        for (final leg in rawLegs)
          OpportunityLeg.fromMap(Map<String, dynamic>.from(leg as Map)),
      ],
      totalStake: (data['totalStake'] as num? ?? 0).toDouble(),
      guaranteedProfit: (data['guaranteedProfit'] as num? ?? 0).toDouble(),
      roiPercent: (data['roiPercent'] as num? ?? 0).toDouble(),
      boostIds: [
        for (final id in (data['boostIds'] as List? ?? const [])) id as String,
      ],
      raw: data,
    );
  }
}

/// A boost as shown on a hedge card (a snapshot taken by the Cloud Function).
class GroupBoost {
  const GroupBoost({
    required this.id,
    required this.sportsbook,
    required this.nickname,
    required this.percentBoost,
    required this.betType,
    required this.maxBet,
    required this.used,
  });

  final String id;
  final Sportsbook sportsbook;
  final String? nickname;
  final double percentBoost;
  final BetType betType;
  final double maxBet;
  final bool used;

  bool get hasNickname => nickname != null && nickname!.trim().isNotEmpty;

  /// The nickname if there is one, otherwise "+25% NFL".
  String get title => hasNickname
      ? nickname!.trim()
      : '+${formatPercent(percentBoost)} ${betType.label}';

  /// "DraftKings +25% NFL, max $50"
  String get details =>
      '${sportsbook.label} +${formatPercent(percentBoost)} ${betType.label}, '
      'max ${formatMoney(maxBet)}';

  factory GroupBoost.fromMap(Map<String, dynamic> data) => GroupBoost(
        id: data['id'] as String? ?? '',
        sportsbook: Sportsbook.values.asNameMap()[data['bookmaker']] ??
            Sportsbook.draftkings,
        nickname: data['nickname'] as String?,
        percentBoost: (data['percentBoost'] as num? ?? 0).toDouble(),
        betType: BetType.values.asNameMap()[data['betType']] ?? BetType.other,
        maxBet: (data['maxBet'] as num? ?? 0).toDouble(),
        used: data['used'] as bool? ?? false,
      );
}

/// One boost combination's hedges under one rounding mode.
class ModeResult {
  const ModeResult({
    required this.bestProfit,
    required this.betCount,
    required this.bets,
  });

  final double bestProfit;
  final int betCount; // total found (bets holds up to the top 25)
  final List<Opportunity> bets;

  static const empty = ModeResult(bestProfit: 0, betCount: 0, bets: []);

  factory ModeResult.fromMap(Map<String, dynamic> data) => ModeResult(
        bestProfit: (data['bestProfit'] as num? ?? 0).toDouble(),
        betCount: (data['betCount'] as num? ?? 0).toInt(),
        bets: [
          for (final item in (data['bets'] as List? ?? const []))
            Opportunity.fromMap(Map<String, dynamic>.from(item as Map)),
        ],
      );
}

/// One boost combination (a single boost, or a DraftKings + FanDuel pair)
/// and its best hedges, most profitable first. The Cloud Function stores
/// results for every rounding mode; [withMode] picks one to show.
class HedgeGroup {
  const HedgeGroup({
    required this.id,
    required this.isTwoWay,
    required this.boosts,
    required this.usedBoostIds,
    required this.mode,
    required this.results,
  });

  final String id;
  final bool isTwoWay; // both sides boosted
  final List<GroupBoost> boosts;
  final List<String> usedBoostIds; // boosts on this card marked used

  /// The rounding mode whose bets this object shows.
  final RoundingMode mode;
  final Map<RoundingMode, ModeResult> results;

  ModeResult get _current => results[mode] ?? ModeResult.empty;
  double get bestProfit => _current.bestProfit;
  int get betCount => _current.betCount;
  List<Opportunity> get bets => _current.bets;

  /// The same boost combination, showing bets for [newMode].
  HedgeGroup withMode(RoundingMode newMode) => HedgeGroup(
        id: id,
        isTwoWay: isTwoWay,
        boosts: boosts,
        usedBoostIds: usedBoostIds,
        mode: newMode,
        results: results,
      );

  /// True when any boost on this card has been marked used.
  bool get usesUsedBoost => usedBoostIds.isNotEmpty;

  bool containsBoost(String boostId) => boosts.any((b) => b.id == boostId);

  /// Lowercase text about the boosts themselves, for search.
  String get boostSearchText => [
        for (final b in boosts) ...[
          b.title,
          b.sportsbook.label,
          b.betType.label,
          b.betType.category.label,
        ],
      ].join(' ').toLowerCase();

  factory HedgeGroup.fromFirestore(String id, Map<String, dynamic> data) {
    final results = <RoundingMode, ModeResult>{};
    final rawModes = data['modes'];
    if (rawModes is Map) {
      for (final entry in rawModes.entries) {
        results[RoundingMode.fromName(entry.key)] = ModeResult.fromMap(
            Map<String, dynamic>.from(entry.value as Map));
      }
    } else {
      // Saved before rounding modes existed: top-level is "no rounding".
      results[RoundingMode.none] = ModeResult.fromMap(data);
    }
    return HedgeGroup(
      id: id,
      isTwoWay: data['type'] == 'two_way',
      boosts: [
        for (final b in (data['boosts'] as List? ?? const []))
          GroupBoost.fromMap(Map<String, dynamic>.from(b as Map)),
      ],
      usedBoostIds: [
        for (final i in (data['usedBoostIds'] as List? ?? const [])) i as String,
      ],
      mode: RoundingMode.none,
      results: results,
    );
  }
}

/// The result of the most recent hedge check, written by the Cloud Function.
class RefreshStatus {
  const RefreshStatus({
    required this.lastRunAt,
    required this.ok,
    required this.message,
    required this.requestsRemaining,
    required this.quotaRemaining,
    required this.quotaUsed,
  });

  final DateTime? lastRunAt;
  final bool ok;
  final String message;
  final String? requestsRemaining; // The Odds API quota left (as text)

  /// The Odds API credits left and used this month (null until the first
  /// odds check after this update).
  final int? quotaRemaining;
  final int? quotaUsed;

  /// The monthly plan size, worked out from used + remaining (500 on the
  /// free plan), so the bar stays correct if the plan changes.
  int? get quotaTotal => quotaRemaining == null
      ? null
      : quotaRemaining! + (quotaUsed ?? 0);

  factory RefreshStatus.fromMap(Map<String, dynamic> data) => RefreshStatus(
        lastRunAt:
            data['lastRunAt'] == null ? null : readDate(data['lastRunAt']),
        ok: data['ok'] as bool? ?? true,
        message: data['message'] as String? ?? '',
        requestsRemaining: data['requestsRemaining']?.toString(),
        quotaRemaining: (data['quotaRemaining'] as num?)?.toInt(),
        quotaUsed: (data['quotaUsed'] as num?)?.toInt(),
      );
}
