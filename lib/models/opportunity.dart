import '../utils/format.dart';
import 'firestore_dates.dart';
import 'hedge_goal.dart';
import 'profit_boost.dart';
import 'rounding.dart';

/// One of the two bets in a hedge (stake and payout are the safest version's).
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
  final String selection; // e.g. "Chiefs -3.5", "Travis Kelce Over 62.5 receiving yards"
  final int odds;
  final double stake;
  final double payout; // total return if this bet wins, stake included
  final double boostPercent; // 0 when this leg isn't boosted
  final String? boostId; // which boost this leg uses, if any

  bool get isBoosted => boostPercent > 0;

  OpportunityLeg withAmounts(double newStake, double newPayout) => OpportunityLeg(
        sportsbook: sportsbook,
        selection: selection,
        odds: odds,
        stake: newStake,
        payout: newPayout,
        boostPercent: boostPercent,
        boostId: boostId,
      );

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

/// One way to stake a hedge: safest, balanced, or most upside.
class HedgeVersion {
  const HedgeVersion({
    required this.key,
    required this.stakes,
    required this.payouts,
    required this.totalStake,
    required this.guaranteedProfit,
    required this.maxProfit,
  });

  final String key; // "safest", "balanced", "upside"
  final List<double> stakes; // per leg, same order as Opportunity.legs
  final List<double> payouts; // per leg, if that bet wins
  final double totalStake;
  final double guaranteedProfit; // the least you can make
  final double maxProfit; // the most you can make

  String get label => switch (key) {
        'balanced' => 'Balanced',
        'upside' => 'Most upside',
        _ => 'Safest',
      };

  factory HedgeVersion.fromMap(Map<String, dynamic> data) {
    final legs = [
      for (final leg in (data['legs'] as List? ?? const []))
        Map<String, dynamic>.from(leg as Map),
    ];
    return HedgeVersion(
      key: data['key'] as String? ?? 'safest',
      stakes: [for (final l in legs) (l['stake'] as num? ?? 0).toDouble()],
      payouts: [for (final l in legs) (l['payout'] as num? ?? 0).toDouble()],
      totalStake: (data['totalStake'] as num? ?? 0).toDouble(),
      guaranteedProfit: (data['guaranteedProfit'] as num? ?? 0).toDouble(),
      maxProfit: (data['maxProfit'] as num? ?? 0).toDouble(),
    );
  }

  Map<String, dynamic> toMap() => {
        'key': key,
        'legs': [
          for (var i = 0; i < stakes.length; i++)
            {'stake': stakes[i], 'payout': payouts[i]},
        ],
        'totalStake': totalStake,
        'guaranteedProfit': guaranteedProfit,
        'maxProfit': maxProfit,
      };
}

/// A hedge: two bets to place on one game, in up to three stake versions.
class Opportunity {
  const Opportunity({
    required this.id,
    required this.betType,
    required this.game,
    required this.commenceTime,
    required this.marketLabel,
    required this.legs,
    required this.versions,
    required this.middle,
    required this.boostIds,
    required this.raw,
  });

  final String id;
  final BetType betType;
  final String game; // "Away @ Home"
  final DateTime commenceTime;
  final String marketLabel; // "moneyline", "total", "receiving yards"...
  final List<OpportunityLeg> legs;
  final List<HedgeVersion> versions; // safest first; duplicates removed
  final String? middle; // when both bets can win, e.g. "...exactly 53"
  final List<String> boostIds; // the boost(s) this hedge uses
  final Map<String, dynamic> raw; // as stored, saved on boosts when placed

  HedgeVersion get safest => versions.first;

  /// The version with the most possible profit.
  HedgeVersion get mostUpside => versions.reduce(
      (a, b) => b.maxProfit > a.maxProfit ? b : a);

  HedgeVersion versionFor(HedgeGoal goal) =>
      goal == HedgeGoal.guaranteed ? safest : mostUpside;

  HedgeVersion versionByKey(String? key) =>
      versions.firstWhere((v) => v.key == key, orElse: () => safest);

  double get guaranteedProfit => safest.guaranteedProfit;
  double get bestMaxProfit => mostUpside.maxProfit;
  double get totalStake => safest.totalStake;

  /// The value this hedge is ranked by for [goal].
  double valueFor(HedgeGoal goal) =>
      goal == HedgeGoal.guaranteed ? guaranteedProfit : bestMaxProfit;

  /// The legs with [version]'s stakes and payouts.
  List<OpportunityLeg> legsFor(HedgeVersion version) => [
        for (var i = 0; i < legs.length; i++)
          legs[i].withAmounts(
            i < version.stakes.length ? version.stakes[i] : legs[i].stake,
            i < version.payouts.length ? version.payouts[i] : legs[i].payout,
          ),
      ];

  /// What's saved on a boost when this hedge is placed with [version].
  Map<String, dynamic> placedRecord(HedgeVersion version) {
    final legMaps = [
      for (var i = 0; i < legs.length; i++)
        {
          ...Map<String, dynamic>.from((raw['legs'] as List)[i] as Map),
          'stake': version.stakes[i],
          'payout': version.payouts[i],
        },
    ];
    return {
      ...raw,
      'legs': legMaps,
      'versions': [version.toMap()],
      'totalStake': version.totalStake,
      'guaranteedProfit': version.guaranteedProfit,
      'maxProfit': version.maxProfit,
      'bestMaxProfit': version.maxProfit,
      'placedVersion': version.key,
    };
  }

  /// Lowercase text the search bar matches against.
  String get searchText => [
        game,
        betType.label,
        betType.category.label,
        marketLabel,
        for (final leg in legs) ...[leg.selection, leg.sportsbook.label],
      ].join(' ').toLowerCase();

  factory Opportunity.fromMap(Map<String, dynamic> data) {
    final legs = [
      for (final leg in (data['legs'] as List? ?? const []))
        OpportunityLeg.fromMap(Map<String, dynamic>.from(leg as Map)),
    ];
    var versions = [
      for (final v in (data['versions'] as List? ?? const []))
        HedgeVersion.fromMap(Map<String, dynamic>.from(v as Map)),
    ];
    if (versions.isEmpty) {
      // Saved before stake versions existed: one version from the top level.
      final guaranteed = (data['guaranteedProfit'] as num? ?? 0).toDouble();
      versions = [
        HedgeVersion(
          key: 'safest',
          stakes: [for (final l in legs) l.stake],
          payouts: [for (final l in legs) l.payout],
          totalStake: (data['totalStake'] as num? ?? 0).toDouble(),
          guaranteedProfit: guaranteed,
          maxProfit: (data['maxProfit'] as num? ?? guaranteed).toDouble(),
        ),
      ];
    }
    return Opportunity(
      id: data['id'] as String? ?? '',
      betType: BetType.values.asNameMap()[data['betType']] ?? BetType.other,
      game: data['game'] as String? ?? '',
      commenceTime: readDate(data['commenceTime']),
      marketLabel: data['marketLabel'] as String? ?? '',
      legs: legs,
      versions: versions,
      middle: data['middle'] as String?,
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
    required this.validFrom,
    required this.eventName,
  });

  final String id;
  final Sportsbook sportsbook;
  final String? nickname;
  final double percentBoost;
  final BetType betType;
  final double maxBet;
  final bool used;
  final DateTime? validFrom;
  final String? eventName; // set for single-game boosts

  bool get hasNickname => nickname != null && nickname!.trim().isNotEmpty;

  /// The nickname if there is one, otherwise "+25% NFL".
  String get title => hasNickname
      ? nickname!.trim()
      : '+${formatPercent(percentBoost)} ${betType.label}';

  /// "DraftKings +25% NFL, max $50" (plus the game, for single-game boosts)
  String get details =>
      '${sportsbook.label} +${formatPercent(percentBoost)} ${betType.label}, '
      'max ${formatMoney(maxBet)}'
      '${eventName == null ? '' : ', $eventName only'}';

  factory GroupBoost.fromMap(Map<String, dynamic> data) => GroupBoost(
        id: data['id'] as String? ?? '',
        sportsbook: Sportsbook.values.asNameMap()[data['bookmaker']] ??
            Sportsbook.draftkings,
        nickname: data['nickname'] as String?,
        percentBoost: (data['percentBoost'] as num? ?? 0).toDouble(),
        betType: BetType.values.asNameMap()[data['betType']] ?? BetType.other,
        maxBet: (data['maxBet'] as num? ?? 0).toDouble(),
        used: data['used'] as bool? ?? false,
        validFrom:
            data['validFrom'] == null ? null : readDate(data['validFrom']),
        eventName: data['eventName'] as String?,
      );
}

/// One boost combination's hedges under one rounding mode.
class ModeResult {
  const ModeResult({
    required this.bestProfit,
    required this.bestMaxProfit,
    required this.betCount,
    required this.bets,
  });

  final double bestProfit;
  final double bestMaxProfit;
  final int betCount; // total found (bets holds the best ones)
  final List<Opportunity> bets;

  static const empty =
      ModeResult(bestProfit: 0, bestMaxProfit: 0, betCount: 0, bets: []);

  factory ModeResult.fromMap(Map<String, dynamic> data) {
    final bets = [
      for (final item in (data['bets'] as List? ?? const []))
        Opportunity.fromMap(Map<String, dynamic>.from(item as Map)),
    ];
    final bestProfit = (data['bestProfit'] as num? ?? 0).toDouble();
    return ModeResult(
      bestProfit: bestProfit,
      bestMaxProfit: (data['bestMaxProfit'] as num? ?? bestProfit).toDouble(),
      betCount: (data['betCount'] as num? ?? 0).toInt(),
      bets: bets,
    );
  }
}

/// One boost combination (a single boost, or a pair) and its best hedges.
/// The Cloud Function stores results for every rounding mode; [withMode]
/// picks one, and [betsFor] sorts them by the chosen goal.
class HedgeGroup {
  const HedgeGroup({
    required this.id,
    required this.isTwoWay,
    required this.boosts,
    required this.usedBoostIds,
    required this.upcoming,
    required this.availableFrom,
    required this.mode,
    required this.results,
  });

  final String id;
  final bool isTwoWay; // both bets boosted
  final List<GroupBoost> boosts;
  final List<String> usedBoostIds; // boosts on this card marked used

  /// A boost on this card has a window that hasn't opened yet.
  final bool upcoming;
  final DateTime? availableFrom;

  /// The rounding mode whose bets this object shows.
  final RoundingMode mode;
  final Map<RoundingMode, ModeResult> results;

  ModeResult get _current => results[mode] ?? ModeResult.empty;
  double get bestProfit => _current.bestProfit;
  double get bestMaxProfit => _current.bestMaxProfit;
  int get betCount => _current.betCount;
  List<Opportunity> get bets => _current.bets;

  double valueFor(HedgeGoal goal) =>
      goal == HedgeGoal.guaranteed ? bestProfit : bestMaxProfit;

  /// This mode's bets, best first for [goal].
  List<Opportunity> betsFor(HedgeGoal goal) =>
      [...bets]..sort((a, b) => b.valueFor(goal).compareTo(a.valueFor(goal)));

  /// The same boost combination, showing bets for [newMode].
  HedgeGroup withMode(RoundingMode newMode) => HedgeGroup(
        id: id,
        isTwoWay: isTwoWay,
        boosts: boosts,
        usedBoostIds: usedBoostIds,
        upcoming: upcoming,
        availableFrom: availableFrom,
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
          if (b.eventName != null) b.eventName!,
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
      // Saved before rounding modes existed: top-level is the default mode.
      results[RoundingMode.small] = ModeResult.fromMap(data);
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
      upcoming: data['upcoming'] as bool? ?? false,
      availableFrom: data['availableFrom'] == null
          ? null
          : readDate(data['availableFrom']),
      mode: RoundingMode.small,
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
    required this.quotaRemaining,
    required this.quotaUsed,
  });

  final DateTime? lastRunAt;
  final bool ok;
  final String message;

  /// The Odds API credits left and used this month (null until the first
  /// odds check that reports them).
  final int? quotaRemaining;
  final int? quotaUsed;

  /// The monthly plan size, worked out from used + remaining.
  int? get quotaTotal => quotaRemaining == null
      ? null
      : quotaRemaining! + (quotaUsed ?? 0);

  factory RefreshStatus.fromMap(Map<String, dynamic> data) => RefreshStatus(
        lastRunAt:
            data['lastRunAt'] == null ? null : readDate(data['lastRunAt']),
        ok: data['ok'] as bool? ?? true,
        message: data['message'] as String? ?? '',
        quotaRemaining: (data['quotaRemaining'] as num?)?.toInt(),
        quotaUsed: (data['quotaUsed'] as num?)?.toInt(),
      );
}
