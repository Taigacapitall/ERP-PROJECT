"""Solde de stock d'un article dans un entrepot, valorise au cout moyen pondere (CUMP).

TODO(A_VALIDER): la methode de valorisation (cout moyen pondere ou FIFO) et la regle d'arrondi
doivent etre confirmees par un comptable (voir HANDOFF, points A_VALIDER).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from vorpal.core.money import Currency
from vorpal.modules.stock.domain.errors import StockConflictError, StockValidationError
from vorpal.modules.stock.domain.values import ZERO, round_amount

_COST_DISPLAY_STEP = Decimal("0.0001")


@dataclass
class StockLevel:
    """Quantite et valeur en stock ; le stock negatif n'est pas supporte."""

    item_code: str
    warehouse_code: str
    currency: Currency
    quantity: Decimal = ZERO
    value: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        if self.quantity < ZERO:
            raise StockValidationError("La quantite en stock ne peut pas etre negative.")
        if self.value < ZERO:
            raise StockValidationError("La valeur du stock ne peut pas etre negative.")

    @property
    def average_cost(self) -> Decimal | None:
        """Cout moyen unitaire (affichage, 4 decimales) ou None si le stock est vide."""
        if self.quantity <= ZERO:
            return None
        return (self.value / self.quantity).quantize(_COST_DISPLAY_STEP, rounding=ROUND_HALF_UP)

    def receive_value(self, quantity: Decimal, value: Decimal) -> None:
        """Ajoute une quantite avec une valeur deja determinee (transfert entrant, ajustement)."""
        if quantity <= ZERO:
            raise StockValidationError("La quantite recue doit etre strictement positive.")
        if value < ZERO:
            raise StockValidationError("La valeur recue ne peut pas etre negative.")
        self.quantity += quantity
        self.value += value

    def receive(self, quantity: Decimal, unit_cost: Decimal) -> Decimal:
        """Entre `quantity` au `unit_cost` ; retourne la valeur entree (arrondie a 2 decimales)."""
        if unit_cost < ZERO:
            raise StockValidationError("Le cout unitaire ne peut pas etre negatif.")
        value_in = round_amount(quantity * unit_cost)
        self.receive_value(quantity, value_in)
        return value_in

    def issue(self, quantity: Decimal) -> Decimal:
        """Sort `quantity` au cout moyen ; retourne la valeur sortie.

        Sortir tout le stock ramene la valeur a exactement zero (aucune derive d'arrondi).
        """
        if quantity <= ZERO:
            raise StockValidationError("La quantite sortie doit etre strictement positive.")
        if quantity > self.quantity:
            raise StockConflictError(
                f"Stock insuffisant pour l'article '{self.item_code}' dans l'entrepot "
                f"'{self.warehouse_code}' : disponible {self.quantity}, demande {quantity}."
            )
        if quantity == self.quantity:
            value_out = self.value
        else:
            value_out = round_amount(self.value * quantity / self.quantity)
        self.quantity -= quantity
        self.value -= value_out
        return value_out

    def revalue(self, amount: Decimal) -> None:
        """Ajoute `amount` a la valeur du stock restant sans changer la quantite (frais d'approche)."""
        if self.quantity <= ZERO:
            raise StockConflictError(
                f"Aucun stock a revaloriser pour l'article '{self.item_code}' "
                f"dans l'entrepot '{self.warehouse_code}'."
            )
        new_value = round_amount(self.value + amount)
        if new_value < ZERO:
            raise StockConflictError("La revalorisation rendrait la valeur du stock negative.")
        self.value = new_value

    def adjust_to(self, counted: Decimal, unit_cost: Decimal | None) -> tuple[Decimal, Decimal]:
        """Ramene la quantite a `counted` ; retourne (ecart de quantite, ecart de valeur).

        Un ecart negatif sort au cout moyen. Un ecart positif entre au cout moyen s'il y a du
        stock, sinon au `unit_cost` fourni (obligatoire : aucune valeur n'est inventee).
        """
        if counted < ZERO:
            raise StockValidationError("La quantite comptee ne peut pas etre negative.")
        variance = counted - self.quantity
        if variance == ZERO:
            return ZERO, Decimal("0.00")
        if variance < ZERO:
            value_out = self.issue(-variance)
            return variance, -value_out
        if self.quantity > ZERO:
            value_in = round_amount(variance * self.value / self.quantity)
        elif unit_cost is not None:
            value_in = round_amount(variance * unit_cost)
        else:
            raise StockValidationError(
                f"Cout unitaire requis pour l'article '{self.item_code}' : "
                "le stock theorique est nul, l'ecart positif ne peut pas etre valorise."
            )
        self.receive_value(variance, value_in)
        return variance, value_in
