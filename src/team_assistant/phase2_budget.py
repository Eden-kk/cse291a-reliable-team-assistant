"""Physical inference accounting shared across independent experimental arms."""
class PhysicalProvider:
    def __init__(self, provider, budget, reservation=60000):
        self.provider, self.budget, self.reservation = provider, budget, reservation
        self.records = provider.records

    async def complete(self, instruction, request):
        self.budget.reserve(self.reservation)
        actual = None
        try:
            outcome = await self.provider.complete(instruction, request)
            actual = outcome.tokens
            return outcome
        finally:
            self.budget.settle(self.reservation, actual)
