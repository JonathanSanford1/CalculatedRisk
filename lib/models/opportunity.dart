import '../utils/format.dart';
import 'firestore_dates.dart';
import 'profit_boost.dart';

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

/// One boost combination (a single boost, or a DraftKings + FanDuel pair)
/// and its best hedges, most profitable first.
class HedgeGroup {
  const HedgeGroup({
    required this.id,
    required this.isTwoWay,
    required this.boosts,
    required this.bestProfit,
    required this.betCount,
    required this.bets,
    required this.usedBoostIds,
  });

  final String id;
  final bool isTwoWay; // both sides boosted
  final List<GroupBoost> boosts;
  final double bestProfit;
  final int betCount; // total found (bets holds up to the top 25)
  final List<Opportunity> bets;
  final List<String> usedBoostIds; // boosts on this card marked used

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
    List<Map<String, dynamic>> maps(Object? raw) => [
          for (final item in (raw as List? ?? const []))
            Map<String, dynamic>.from(item as Map),
        ];
    return HedgeGroup(
      id: id,
      isTwoWay: data['type'] == 'two_way',
      boosts: maps(data['boosts']).map(GroupBoost.fromMap).toList(),
      bestProfit: (data['bestProfit'] as num? ?? 0).toDouble(),
      betCount: (data['betCount'] as num? ?? 0).toInt(),
      bets: maps(data['bets']).map(Opportunity.fromMap).toList(),
      usedBoostIds: [
        for (final id in (data['usedBoostIds'] as List? ?? const []))
          id as String,
      ],
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
  });

  final DateTime? lastRunAt;
  final bool ok;
  final String message;
  final String? requestsRemaining; // The Odds API quota left

  factory RefreshStatus.fromMap(Map<String, dynamic> data) => RefreshStatus(
        lastRunAt:
            data['lastRunAt'] == null ? null : readDate(data['lastRunAt']),
        ok: data['ok'] as bool? ?? true,
        message: data['message'] as String? ?? '',
        requestsRemaining: data['requestsRemaining']?.toString(),
      );
}
