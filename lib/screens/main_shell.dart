import 'package:flutter/material.dart';

import '../services/boost_repository.dart';
import 'boosts_page.dart';
import 'hedges_page.dart';
import '../widgets/brand_app_bar.dart';

/// App frame with the Boosts and Hedges tabs.
class MainShell extends StatefulWidget {
  const MainShell({super.key, required this.repository});

  final BoostRepository repository;

  @override
  State<MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<MainShell> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: const BrandAppBar(),
      // IndexedStack keeps both tabs alive, so switching doesn't reload data.
      body: IndexedStack(
        index: _tab,
        children: [
          BoostsPage(repository: widget.repository),
          HedgesPage(repository: widget.repository),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (index) => setState(() => _tab = index),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.local_offer_outlined),
            selectedIcon: Icon(Icons.local_offer),
            label: 'Boosts',
          ),
          NavigationDestination(
            icon: Icon(Icons.swap_horiz),
            label: 'Hedges',
          ),
        ],
      ),
    );
  }
}
