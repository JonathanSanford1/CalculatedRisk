import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// The CalculatedRisk title bar: a dark navy bar with a green-to-blue logo
/// mark and stripe (DraftKings green to FanDuel blue) and a bold wordmark.
class BrandAppBar extends StatelessWidget implements PreferredSizeWidget {
  const BrandAppBar({super.key});

  static const navy = Color(0xFF14213D);
  static const _stripeHeight = 4.0;

  static final _brandGradient = LinearGradient(
    colors: [Colors.green.shade500, Colors.blue.shade500],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );

  @override
  Size get preferredSize =>
      const Size.fromHeight(kToolbarHeight + _stripeHeight);

  @override
  Widget build(BuildContext context) {
    return AppBar(
      backgroundColor: navy,
      foregroundColor: Colors.white,
      elevation: 0,
      scrolledUnderElevation: 0,
      // White clock/battery icons on the dark bar.
      systemOverlayStyle: SystemUiOverlayStyle.light,
      centerTitle: false,
      titleSpacing: 16,
      title: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(9),
            child: Image.asset(
              'assets/IMG_6015.PNG',
              width: 34,
              height: 34,
              fit: BoxFit.cover,
            ),
          ),
          const SizedBox(width: 10),
          const Text.rich(
            TextSpan(
              children: [
                TextSpan(
                  text: 'Calculated',
                  style: TextStyle(fontWeight: FontWeight.w400),
                ),
                TextSpan(
                  text: 'Risk',
                  style: TextStyle(fontWeight: FontWeight.w900),
                ),
              ],
            ),
            style: TextStyle(
              color: Colors.white,
              fontSize: 24,
              letterSpacing: 0.4,
            ),
          ),
        ],
      ),
      bottom: PreferredSize(
        preferredSize: const Size.fromHeight(_stripeHeight),
        child: Container(
          height: _stripeHeight,
          decoration: BoxDecoration(gradient: _brandGradient),
        ),
      ),
    );
  }
}
