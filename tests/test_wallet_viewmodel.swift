import Foundation

@main
struct WalletViewModelTests {
    @MainActor
    static func main() {
        let suite = "AirCardWalletTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let a = String(repeating: "A", count: 27) + "="
        let b = String(repeating: "B", count: 27) + "="
        let c = String(repeating: "C", count: 27) + "="
        defaults.set([b, a, b], forKey: "mak5er.aircard.savedCards")
        let vm = AppViewModel(cardDefaults: defaults, connectOnLaunch: false)
        precondition(vm.cards.map(\.id) == [b, a])
        precondition(vm.currentVerifiedCards.isEmpty) // Saved records stay hidden until this scan sees them.
        precondition(vm.confirmedCardIDs.isEmpty) // Legacy IDs have no device provenance.
        vm.activateCardDevice("first-phone")
        vm.isScanningCards = true
        vm.walletCatalog = WalletCatalog(paymentStatus: "matched", payments: [
            .init(id: a, name: "Active A", source: "payment"),
            .init(id: b, name: "Active B", source: "payment")
        ], memberships: [], warnings: [], cacheUpdatedAt: nil)
        vm.reconcileMatchedPaymentCards()
        precondition(vm.currentVerifiedCards.isEmpty) // A cache alone is not enough.
        vm.recordScannedCard(a)
        vm.recordScannedCard(a)
        precondition(vm.currentScanIDs == [a] && vm.confirmedCardIDs == [a, b])
        precondition(vm.currentVerifiedCardIDs == [a, b])
        precondition(vm.cards.count == 2)
        vm.isScanningCards = false
        let activation = "A00000000310100100000020"
        precondition(!vm.recordActivatedPaymentCard(activation))
        vm.walletCatalog = WalletCatalog(paymentStatus: "matched", payments: [.init(id: b, name: "Active B", source: "payment", activationID: activation)], memberships: [], warnings: [], cacheUpdatedAt: nil)
        vm.reconcilePendingPaymentActivations()
        precondition(vm.cards.first(where: { $0.id == b })?.displayName == "Active B")
        vm.cards[0].customImageURL = URL(fileURLWithPath: "/skin-b.png")
        vm.cards[1].customImageURL = URL(fileURLWithPath: "/skin-a.png")
        vm.cards.reverse()
        vm.activateCardDevice("second-phone")
        precondition(vm.confirmedCardIDs.isEmpty)
        precondition(vm.cards.allSatisfy { $0.customImageURL == nil })
        vm.recordScannedCard(b)
        vm.activateCardDevice("first-phone")
        precondition(vm.cards.map(\.id) == [a, b])
        precondition(vm.confirmedCardIDs == [a, b])
        precondition(vm.cards[0].customImageURL?.path == "/skin-a.png")
        precondition(vm.cards[1].customImageURL?.path == "/skin-b.png")
        vm.clearAllCards()
        vm.activateCardDevice("second-phone")
        precondition(vm.confirmedCardIDs == [b])
        vm.activateCardDevice("first-phone")
        precondition(vm.cards.isEmpty) // Clear must not resurrect legacy JSON/defaults.
        let relaunched = AppViewModel(cardDefaults: defaults, connectOnLaunch: false)
        relaunched.activateCardDevice("first-phone")
        precondition(relaunched.cards.isEmpty)
        relaunched.activateCardDevice("second-phone")
        precondition(relaunched.confirmedCardIDs == [b])
        let countBeforePreload = relaunched.cards.count
        relaunched.recordPreloadedCard(c)
        relaunched.recordPreloadedCard(c)
        precondition(relaunched.cards.count == countBeforePreload + 1)
        precondition(relaunched.cards.first(where: { $0.id == c })?.confirmed == true)
        precondition(relaunched.currentVerifiedCards.map(\.id) == [c])
        // Number color is opt-in per card, survives relaunch and makes a flashed card pending again.
        precondition(relaunched.cards.allSatisfy { $0.numberColorHex == nil })
        let skinURL = FileManager.default.temporaryDirectory.appendingPathComponent("aircard-skin-\(UUID().uuidString).png")
        try! Data([0x89, 0x50, 0x4E, 0x47]).write(to: skinURL)
        defer { try? FileManager.default.removeItem(at: skinURL) }
        var colored = CardItem(id: c)
        precondition(!colored.hasFlashableChange)
        colored.numberColorHex = "#112233"
        precondition(colored.hasFlashableChange) // a color alone is enough to flash
        colored.numberColorHex = nil
        colored.customImageURL = skinURL // didSet computes the skin signature
        precondition(colored.hasFlashableChange)
        let colorIndex = relaunched.cards.firstIndex(where: { $0.id == c })!
        relaunched.cards[colorIndex].numberColorHex = "#112233"
        let colorRelaunch = AppViewModel(cardDefaults: defaults, connectOnLaunch: false)
        colorRelaunch.activateCardDevice("second-phone")
        precondition(colorRelaunch.cards.first(where: { $0.id == c })?.numberColorHex == "#112233")
        precondition(CardNumberColor.normalized("#a1b2c3") == "#A1B2C3")
        precondition(CardNumberColor.normalized("rgb(1,2,3)") == nil)
        precondition(CardNumberColor.hex(from: CardNumberColor.color(from: "#FF8000")) == "#FF8000")
        // Runs last: assigning cards persists them into the shared test defaults.
        let colorOnly = AppViewModel(cardDefaults: defaults, connectOnLaunch: false)
        var colorOnlyCard = CardItem(id: "color-only")
        colorOnlyCard.numberColorHex = "#FFFFFF"
        colorOnly.cards = [colorOnlyCard]
        precondition(colorOnly.isNumberColorPending(colorOnlyCard))
        precondition(!colorOnly.isSkinPending(colorOnlyCard))
        precondition(colorOnly.cardsNeedingFlash.map(\.id) == ["color-only"])
        print("Wallet view model migration, device isolation, repeat scans, skin identity and clear/relaunch passed")
    }
}
